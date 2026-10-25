/**
 * AudioWorklet: gapless playback of streamed 24 kHz PCM, with an instant
 * barge-in flush. The main thread posts Float32Array chunks; posting "clear"
 * drops everything queued so the caller stops hearing the model immediately.
 *
 * Posts "idle" once when the queue drains after playing, so the UI can switch
 * from "speaking" back to "listening" exactly when the audio ends.
 */
class PcmPlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = []; // Float32Array chunks, oldest first
    this.readIndex = 0; // read offset into queue[0]
    this.playing = false;
    this.port.onmessage = (event) => {
      if (event.data === "clear") {
        this.queue = [];
        this.readIndex = 0;
        this.playing = false;
        return;
      }
      this.queue.push(event.data);
    };
  }

  process(_inputs, outputs) {
    const channel = outputs[0][0];
    for (let i = 0; i < channel.length; i++) {
      if (this.queue.length === 0) {
        channel[i] = 0; // underrun -> silence
        continue;
      }
      const chunk = this.queue[0];
      channel[i] = chunk[this.readIndex++];
      this.playing = true;
      if (this.readIndex >= chunk.length) {
        this.queue.shift();
        this.readIndex = 0;
      }
    }
    if (this.queue.length === 0 && this.playing) {
      this.playing = false;
      this.port.postMessage("idle");
    }
    return true;
  }
}

registerProcessor("pcm-player-processor", PcmPlayerProcessor);
