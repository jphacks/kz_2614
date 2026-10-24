"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../Programs/ServerSide/dashboard/web/voice_pcm_output_worklet.js"), "utf8");

function pcm(values) {
  const buffer = new ArrayBuffer(values.length * 2);
  const view = new DataView(buffer);
  for (let index = 0; index < values.length; index++) view.setInt16(index * 2, values[index], true);
  return buffer;
}

function worklet(rate) {
  const messages = [];
  let Processor;
  class AudioWorkletProcessor {
    constructor() {
      this.port = { postMessage(message) { messages.push(structuredClone(message)); } };
    }
  }
  vm.runInNewContext(source, {
    AudioWorkletProcessor, sampleRate: rate,
    registerProcessor(name, implementation) {
      assert.equal(name, "nen-gitsune-pcm-output");
      Processor = implementation;
    },
  }, { filename: "voice_pcm_output_worklet.js" });
  const processor = new Processor();
  return {
    processor, messages,
    send(message) { processor.port.onmessage({ data: structuredClone(message) }); },
    reset(epoch = 1, turn_id = "turn-one") { this.send({ type: "reset", epoch, turn_id }); },
    audio(values, epoch = 1, turn_id = "turn-one") { this.send({ type: "audio", epoch, turn_id, pcm: pcm(values) }); },
    render(length = 128) {
      const mono = new Float32Array(length).fill(0.75);
      const extra = new Float32Array(length).fill(-0.5);
      assert.equal(processor.process([], [[mono, extra]], {}), true);
      assert.ok(extra.every((value) => value === 0), "extra channels must stay silent");
      return mono;
    },
  };
}

function renderAll(instance, sizes = [128]) {
  const expected = Math.ceil((instance.processor.queuedBytes / 2) * instance.processor.outputRate / 16000);
  const result = [];
  let part = 0;
  while (instance.processor.frameCount > 0) {
    result.push(...instance.render(sizes[part++ % sizes.length]));
    assert.ok(part < 100000, "the final sample must drain in finite time");
  }
  assert.ok(result.length >= expected);
  assert.ok(result.slice(expected).every((value) => value === 0));
  return result.slice(0, expected);
}

function reference(values, rate) {
  return Array.from({ length: Math.ceil(values.length * rate / 16000) }, (_, index) => {
    const position = index * 16000 / rate;
    const first = Math.floor(position);
    const next = Math.min(values.length - 1, first + 1);
    return Math.fround((values[first] + (values[next] - values[first]) * (position - first)) / 32768);
  });
}

function checkRatesAmplitudeOrderAndLastSample() {
  for (const rate of [16000, 44100, 48000]) {
    for (const values of [[32767], [-32768, 0, 16384, -16384, 32767], Array.from({ length: 1600 }, (_, index) => Math.round(25000 * Math.sin(index * 0.073)))]) {
      const instance = worklet(rate);
      instance.reset();
      instance.audio(values);
      assert.deepEqual(instance.messages, [], "enqueue must not claim playback started");
      const actual = renderAll(instance, [1, 127, 128, 39]);
      const expected = reference(values, rate);
      assert.equal(actual.length, expected.length, `${rate}: duration must preserve the final sample`);
      for (let index = 0; index < expected.length; index++) {
        assert.ok(Math.abs(actual[index] - expected[index]) < 1e-6, `${rate}: sample ${index} must preserve gain and order`);
      }
      assert.deepEqual(instance.messages.map((message) => message.type), ["started", "consumed", "drained"]);
      assert.equal(instance.messages[1].bytes, values.length * 2);
      assert.ok(instance.messages.every((message) => message.epoch === 1 && message.turn_id === "turn-one"));
      instance.render();
      assert.equal(instance.messages.length, 3, "empty renders must not repeat drained");
    }
  }
}

function checkFrameAndRenderBoundaryContinuity() {
  const values = Array.from({ length: 6389 }, (_, index) => Math.round(28000 * Math.sin(index * 0.021)));
  for (const rate of [16000, 44100, 48000]) {
    const instance = worklet(rate);
    instance.reset();
    const lengths = [1, 1599, 2, 1600, 817, 1370, 1000];
    let offset = 0;
    for (const length of lengths) {
      instance.audio(values.slice(offset, offset + length));
      offset += length;
    }
    assert.equal(offset, values.length);
    const actual = renderAll(instance, [128, 3, 511, 1]);
    const expected = reference(values, rate);
    assert.equal(actual.length, expected.length);
    assert.ok(actual.every((value, index) => Math.abs(value - expected[index]) < 1e-6));
    assert.deepEqual(instance.messages.filter((message) => message.type === "consumed").map((message) => message.bytes), lengths.map((length) => length * 2));
    assert.equal(instance.messages.filter((message) => message.type === "started").length, 1);
    assert.equal(instance.messages.filter((message) => message.type === "drained").length, 1);
  }
}

function checkResetsStaleFramesAndSuspendedContextFence() {
  const instance = worklet(48000);
  instance.reset();
  instance.audio(new Array(1600).fill(20000));
  assert.ok(instance.render(128).some((value) => value > 0));
  instance.reset(2, "turn-two");
  instance.messages.length = 0;
  instance.audio([32767], 1, "turn-one");
  instance.send({ type: "reset", epoch: 1, turn_id: "turn-one" });
  assert.ok(instance.render().every((value) => value === 0));
  assert.deepEqual(instance.messages, [], "reset must discard old audio without old consumed/drained acknowledgements");
  instance.audio([-32768], 2, "turn-two");
  assert.deepEqual(renderAll(instance), [-1, -1, -1]);
  assert.ok(instance.messages.every((message) => message.epoch === 2 && message.turn_id === "turn-two"));

  // Main performs this null-turn reset before stop/suspend and must not post new
  // audio while AudioContext is non-running (processor has no context.state API).
  instance.reset(3, null);
  instance.messages.length = 0;
  instance.audio([32767], 2, "turn-two");
  instance.audio([32767], 3, null);
  assert.ok(instance.render().every((value) => value === 0));
  assert.equal(instance.processor.queuedBytes, 0);
  assert.deepEqual(instance.messages, []);
  instance.reset(4, "turn-three");
  instance.audio([16384], 4, "turn-three");
  assert.deepEqual(renderAll(instance), [0.5, 0.5, 0.5]);
}

function checkUnderrunAndPartialConsumption() {
  const instance = worklet(48000);
  instance.reset();
  instance.audio([16384]);
  instance.render(2);
  assert.deepEqual(instance.messages.map((message) => message.type), ["started"]);
  assert.equal(instance.processor.queuedBytes, 2, "a partially rendered frame still owns its full byte budget");
  instance.render(1);
  assert.deepEqual(instance.messages.map((message) => message.type), ["started", "consumed", "drained"]);
  assert.ok(instance.render().every((value) => value === 0));
  instance.audio([-16384]);
  assert.deepEqual(renderAll(instance), [-0.5, -0.5, -0.5]);
  assert.equal(instance.messages.filter((message) => message.type === "started").length, 1);
  assert.equal(instance.messages.filter((message) => message.type === "drained").length, 2);
}

function checkInvalidFramesAndBoundedOverflow() {
  for (const bad of [new ArrayBuffer(0), new ArrayBuffer(3), new ArrayBuffer(3202), new Uint8Array(2), null]) {
    const instance = worklet(16000);
    instance.reset();
    instance.send({ type: "audio", epoch: 1, turn_id: "turn-one", pcm: bad });
    assert.equal(instance.messages.length, 1);
    assert.equal(instance.messages[0].type, "error");
    assert.ok(instance.render().every((value) => value === 0));
  }
  const instance = worklet(48000);
  instance.reset();
  const frame = new ArrayBuffer(3200);
  while (instance.messages.length === 0) {
    instance.send({ type: "audio", epoch: 1, turn_id: "turn-one", pcm: frame });
    assert.ok(instance.processor.queuedBytes <= 8 * 1024 * 1024);
    const floatBytes = instance.processor.frames.reduce((sum, value) => sum + (value?.samples.byteLength || 0), 0);
    assert.ok(floatBytes <= 16 * 1024 * 1024);
  }
  assert.equal(instance.messages[0].type, "error");
  assert.match(instance.messages[0].message, /limit/);
  assert.equal(instance.processor.queuedBytes, 0);
  assert.equal(instance.processor.frameCount, 0);
  assert.ok(instance.render().every((value) => value === 0));
  instance.reset(2, "after-overflow");
  instance.audio([1234], 2, "after-overflow");
  assert.ok(renderAll(instance).every((value) => value === 1234 / 32768));
}

checkRatesAmplitudeOrderAndLastSample();
checkFrameAndRenderBoundaryContinuity();
checkResetsStaleFramesAndSuspendedContextFence();
checkUnderrunAndPartialConsumption();
checkInvalidFramesAndBoundedOverflow();
console.log("PCM output worklet passed: rates, linear continuity, amplitudes, final drain, byte acknowledgements, reset/stop fences and bounded overflow");
