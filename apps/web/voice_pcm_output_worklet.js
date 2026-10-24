"use strict";

// Output has the same wire format as ESP32: mono PCM16LE at 16 kHz.
// This processor only renders samples; it makes no speech or VAD decisions.
class NenGitsunePCMOutputProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.outputRate = sampleRate;
    this.inputRate = 16_000;
    this.maxBytes = 8 * 1024 * 1024;
    this.maxFrames = 4096;
    this.epoch = -1;
    this.turnId = null;
    this.enabled = false;
    this.clearQueue();
    this.port.onmessage = (event) => this.receive(event.data);
  }

  clearQueue() {
    this.frames = [];
    this.head = 0;
    this.frameCount = 0;
    this.queuedBytes = 0;
    this.phase = 0;
    this.started = false;
    this.drained = true;
  }

  notify(type, fields = {}) {
    this.port.postMessage({ type, epoch: this.epoch, turn_id: this.turnId, ...fields });
  }

  fail(message) {
    this.clearQueue();
    this.enabled = false;
    this.notify("error", { message });
  }

  receive(message) {
    if (!message || typeof message !== "object") return;
    if (message.type === "reset") {
      if (!Number.isSafeInteger(message.epoch) || message.epoch < 0) return;
      if (message.epoch < this.epoch) return;
      // A turn change requires a new epoch. Late resets must not flush its successor.
      if (message.epoch === this.epoch && message.turn_id !== this.turnId) return;
      if (message.turn_id !== null && (typeof message.turn_id !== "string" || !message.turn_id)) return;
      this.clearQueue();
      this.epoch = message.epoch;
      this.turnId = message.turn_id;
      this.enabled = this.turnId !== null;
      return;
    }
    if (message.type !== "audio" || !this.enabled
        || message.epoch !== this.epoch || message.turn_id !== this.turnId) return;
    const pcm = message.pcm;
    if (Object.prototype.toString.call(pcm) !== "[object ArrayBuffer]"
        || !pcm.byteLength || pcm.byteLength > 3200 || pcm.byteLength % 2) {
      this.fail("Invalid PCM playback frame");
      return;
    }
    if (this.queuedBytes + pcm.byteLength > this.maxBytes || this.frameCount >= this.maxFrames) {
      this.fail("PCM playback queue exceeded its limit");
      return;
    }
    try {
      const view = new DataView(pcm);
      const samples = new Float32Array(pcm.byteLength / 2);
      for (let index = 0; index < samples.length; index++) {
        samples[index] = view.getInt16(index * 2, true) / 32768;
      }
      // Only the float snapshot remains owned here (at most twice the PCM budget).
      this.frames.push({ samples, offset: 0, bytes: pcm.byteLength });
      this.frameCount++;
      this.queuedBytes += pcm.byteLength;
      this.drained = false;
    } catch (_error) {
      this.fail("Invalid PCM playback buffer");
    }
  }

  peekNext(frame) {
    if (frame.offset + 1 < frame.samples.length) return frame.samples[frame.offset + 1];
    const next = this.frames[this.head + 1];
    // Edge extension finishes the last sample even when no later frame arrives.
    return next ? next.samples[next.offset] : frame.samples[frame.offset];
  }

  consumeSample() {
    const frame = this.frames[this.head];
    if (!frame) return;
    if (++frame.offset < frame.samples.length) return;
    this.frames[this.head++] = null;
    this.frameCount--;
    this.queuedBytes -= frame.bytes;
    this.notify("consumed", { bytes: frame.bytes });
    if (this.frameCount === 0) {
      this.frames = [];
      this.head = 0;
      this.phase = 0;
      if (!this.drained) {
        this.drained = true;
        this.notify("drained");
      }
    } else if (this.head > 64 && this.head * 2 >= this.frames.length) {
      this.frames = this.frames.slice(this.head);
      this.head = 0;
    }
  }

  process(_inputs, outputs) {
    for (const output of outputs) for (const channel of output) channel.fill(0);
    const channel = outputs[0]?.[0];
    if (!channel || !this.enabled) return true;
    for (let index = 0; index < channel.length; index++) {
      const frame = this.frames[this.head];
      if (!frame) break;
      if (!this.started) {
        this.started = true;
        this.notify("started");
      }
      const first = frame.samples[frame.offset];
      const next = this.peekNext(frame);
      channel[index] = first + (next - first) * (this.phase / this.outputRate);
      // Integer phase avoids cumulative drift at 44.1 kHz and crosses input
      // frames without resetting interpolation at render-quantum boundaries.
      this.phase += this.inputRate;
      while (this.phase >= this.outputRate && this.frameCount > 0) {
        this.phase -= this.outputRate;
        this.consumeSample();
      }
    }
    return true;
  }
}

registerProcessor("nen-gitsune-pcm-output", NenGitsunePCMOutputProcessor);
