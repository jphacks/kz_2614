"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../Programs/ServerSide/dashboard/web/voice_pcm_worklet.js"), "utf8");
const FRAME_SAMPLES = 4096;
const TARGET_RATE = 16_000;

function worklet(rate) {
  const messages = [];
  let Processor;
  class AudioWorkletProcessor {
    constructor() {
      this.port = { postMessage(message, transfer) {
        assert.equal(message.type, "pcm");
        assert.equal(message.pcm.byteLength, FRAME_SAMPLES * 2);
        assert.equal(transfer.length, 1);
        assert.equal(transfer[0], message.pcm);
        assert.ok(Number.isFinite(message.level) && message.level >= 0 && message.level <= 1);
        // Real transfer semantics expose accidental reuse of detached buffers.
        messages.push(structuredClone(message, { transfer }));
      } };
    }
  }
  vm.runInNewContext(source, {
    AudioWorkletProcessor, sampleRate: rate,
    registerProcessor(name, implementation) {
      assert.equal(name, "nen-gitsune-pcm");
      assert.equal(Processor, undefined);
      Processor = implementation;
    },
  }, { filename: "voice_pcm_worklet.js" });
  assert.equal(typeof Processor, "function");
  const processor = new Processor();
  return {
    messages,
    process(channels, length = channels[0]?.length || 128) {
      const outputs = [[new Float32Array(length).fill(0.75), new Float32Array(length).fill(-0.5)]];
      assert.equal(processor.process([channels], outputs, {}), true);
      for (const channel of outputs[0]) assert.ok(channel.every((value) => value === 0), "The output must remain silent");
    },
  };
}

function feed(instance, channels, splitSizes = [128]) {
  const length = channels[0].length;
  let offset = 0, part = 0;
  while (offset < length) {
    const end = Math.min(length, offset + splitSizes[part++ % splitSizes.length]);
    instance.process(channels.map((channel) => channel.subarray(offset, end)));
    offset = end;
  }
}

function bytes(instance) {
  return Buffer.concat(instance.messages.map((message) => Buffer.from(message.pcm)));
}

function tone(rate, frequency, length = rate) {
  return Float32Array.from({ length }, (_, index) => 0.8 * Math.sin((2 * Math.PI * frequency * index) / rate));
}

function pcmRms(buffer, skipSamples = 0) {
  let squared = 0;
  const count = buffer.length / 2;
  for (let index = skipSamples; index < count; index++) {
    const value = buffer.readInt16LE(index * 2) / 32768;
    squared += value * value;
  }
  return Math.sqrt(squared / (count - skipSamples));
}

function checkByteOrderClippingAndMono() {
  const instance = worklet(16_000);
  const pattern = [0, 1, -1, 0.5, -0.5, 2, -2, NaN];
  const samples = Float32Array.from({ length: FRAME_SAMPLES }, (_, index) => pattern[index % pattern.length]);
  feed(instance, [samples], [1, 127, 128, 311]);
  assert.equal(instance.messages.length, 1);
  const result = bytes(instance);
  assert.deepEqual(Array.from({ length: pattern.length }, (_, index) => result.readInt16LE(index * 2)), [0, 32767, -32768, 16384, -16384, 32767, -32768, 0]);
  assert.equal(result.subarray(2, 6).toString("hex"), "ff7f0080", "Signed samples must be explicitly little-endian");

  const stereo = worklet(16_000);
  feed(stereo, [new Float32Array(FRAME_SAMPLES).fill(0.5), new Float32Array(FRAME_SAMPLES).fill(-0.25)]);
  assert.ok(Array.from({ length: FRAME_SAMPLES }, (_, index) => bytes(stereo).readInt16LE(index * 2)).every((value) => value === 4096));
  assert.equal(stereo.messages[0].level, 0.125);
}

function checkRatesCountsAndFrameBoundaries() {
  for (const rate of [16_000, 48_000, 44_100]) {
    const instance = worklet(rate);
    const firstFrameBoundary = Math.ceil((FRAME_SAMPLES * rate) / TARGET_RATE);
    feed(instance, [new Float32Array(firstFrameBoundary - 1)], [127, 128, 1, 263]);
    assert.equal(instance.messages.length, 0, `${rate}: a frame must not be emitted early`);
    instance.process([new Float32Array(1)]);
    assert.equal(instance.messages.length, 1, `${rate}: the exact boundary must emit a frame`);

    const twoSeconds = worklet(rate);
    feed(twoSeconds, [new Float32Array(rate * 2)], [128, 39, 511, 1, 256]);
    assert.equal(twoSeconds.messages.length, Math.floor((TARGET_RATE * 2) / FRAME_SAMPLES), `${rate}: exact sample-rate conversion count`);
    assert.ok(twoSeconds.messages.every((message) => message.level === 0 && Buffer.from(message.pcm).every((value) => value === 0)), `${rate}: silence must produce full PCM frames`);
  }
}

function checkSplitContinuity() {
  for (const rate of [16_000, 48_000, 44_100]) {
    const signal = Float32Array.from({ length: rate * 2 + 233 }, (_, index) => (
      0.5 * Math.sin((2 * Math.PI * 537 * index) / rate)
      + 0.15 * Math.sin((2 * Math.PI * 3_317 * index) / rate)
    ));
    const whole = worklet(rate), fragmented = worklet(rate);
    feed(whole, [signal], [signal.length]);
    feed(fragmented, [signal], [1, 2, 127, 128, 511, 73, 256]);
    assert.deepEqual(bytes(fragmented), bytes(whole), `${rate}: filter and fractional phase must continue between process calls`);
    assert.deepEqual(fragmented.messages.map((message) => message.level), whole.messages.map((message) => message.level));
  }
}

function checkAntiAliasAndPassband() {
  for (const rate of [48_000, 44_100]) {
    const low = worklet(rate), high = worklet(rate);
    feed(low, [tone(rate, 1_000)]);
    feed(high, [tone(rate, 12_000)]);
    const lowRms = pcmRms(bytes(low), FRAME_SAMPLES);
    const highRms = pcmRms(bytes(high), FRAME_SAMPLES);
    assert.ok(lowRms > 0.5, `${rate}: speech-band content must survive`);
    assert.ok(highRms < lowRms * 0.02, `${rate}: the out-of-band tone must be attenuated before downsampling`);
  }
}

function checkDisconnectedInputStillSendsSilence() {
  const instance = worklet(16_000);
  for (let quantum = 0; quantum < (FRAME_SAMPLES * 2) / 128; quantum++) instance.process([], 128);
  assert.equal(instance.messages.length, 2);
  assert.ok(bytes(instance).every((value) => value === 0));
}

checkByteOrderClippingAndMono();
checkRatesCountsAndFrameBoundaries();
checkSplitContinuity();
checkAntiAliasAndPassband();
checkDisconnectedInputStillSendsSilence();
console.log("PCM worklet passed: 16/48/44.1 kHz counts, split continuity, little-endian clipping, mono, silence, output zeros, anti-aliasing and transfer ownership");
