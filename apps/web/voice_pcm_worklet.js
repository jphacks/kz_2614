"use strict";

// The browser and ESP32 send the same fixed PCM format: mono, 16 kHz,
// signed 16-bit little-endian, 4096 samples per frame. Silence is sent too.
class NenGitsunePCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.inputRate = sampleRate;
    this.targetRate = 16_000;
    this.frameSamples = 4096;
    this.phase = 0;
    this.previousSample = 0;
    this.frameOffset = 0;
    this.frameSquared = 0;
    this.frame = new DataView(new ArrayBuffer(this.frameSamples * 2));

    // A normalized, windowed-sinc low-pass runs before downsampling, so
    // frequencies above the target Nyquist frequency are not simply folded
    // into the speech band. Its history survives audio render boundaries.
    this.filter = null;
    if (this.inputRate > this.targetRate) {
      const taps = 63;
      const cutoff = (this.targetRate * 0.45) / this.inputRate;
      this.filter = new Float64Array(taps);
      this.history = new Float64Array(taps);
      this.historyOffset = 0;
      let sum = 0;
      for (let index = 0; index < taps; index++) {
        const distance = index - (taps - 1) / 2;
        const sinc = distance === 0
          ? 2 * cutoff
          : Math.sin(2 * Math.PI * cutoff * distance) / (Math.PI * distance);
        const window = 0.54 - 0.46 * Math.cos((2 * Math.PI * index) / (taps - 1));
        this.filter[index] = sinc * window;
        sum += this.filter[index];
      }
      for (let index = 0; index < taps; index++) this.filter[index] /= sum;
    }
  }

  lowPass(sample) {
    if (!this.filter) return sample;
    this.history[this.historyOffset] = sample;
    let cursor = this.historyOffset;
    let filtered = 0;
    for (let tap = 0; tap < this.filter.length; tap++) {
      filtered += this.filter[tap] * this.history[cursor];
      if (--cursor < 0) cursor = this.history.length - 1;
    }
    if (++this.historyOffset === this.history.length) this.historyOffset = 0;
    return filtered;
  }

  emitSample(sample) {
    const clipped = Math.min(1, Math.max(-1, sample));
    const pcm = Math.round(clipped * (clipped < 0 ? 32768 : 32767));
    this.frame.setInt16(this.frameOffset * 2, pcm, true);
    this.frameSquared += clipped * clipped;
    if (++this.frameOffset !== this.frameSamples) return;

    const buffer = this.frame.buffer;
    const level = Math.sqrt(this.frameSquared / this.frameSamples);
    this.port.postMessage({ type: "pcm", pcm: buffer, level }, [buffer]);
    // postMessage transfers ownership; never reuse the detached frame.
    this.frame = new DataView(new ArrayBuffer(this.frameSamples * 2));
    this.frameOffset = 0;
    this.frameSquared = 0;
  }

  process(inputs, outputs) {
    // Connecting the worklet to a destination keeps the graph processing,
    // but microphone samples must never be played through that connection.
    for (const output of outputs) {
      for (const channel of output) channel.fill(0);
    }

    const channels = inputs[0] || [];
    const length = channels[0]?.length || outputs[0]?.[0]?.length || 128;
    for (let index = 0; index < length; index++) {
      let mono = 0;
      for (const channel of channels) {
        const value = channel[index];
        mono += Number.isFinite(value) ? value : 0;
      }
      if (channels.length) mono /= channels.length;
      const filtered = this.lowPass(mono);

      // Integer-rate phase accumulation preserves the exact sample count
      // across 128-sample render quanta, including the 44.1 kHz ratio.
      this.phase += this.targetRate;
      while (this.phase >= this.inputRate) {
        this.phase -= this.inputRate;
        const fraction = 1 - this.phase / this.targetRate;
        this.emitSample(this.previousSample + (filtered - this.previousSample) * fraction);
      }
      this.previousSample = filtered;
    }
    return true;
  }
}

registerProcessor("nen-gitsune-pcm", NenGitsunePCMProcessor);
