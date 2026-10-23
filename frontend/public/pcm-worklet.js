/**
 * AudioWorklet: captures mic input as Float32 frames for PCM streaming.
 * Serves /pcm-worklet.js from the public directory.
 */
class PcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0]) {
      // Copy: the underlying buffer is reused by the audio graph.
      this.port.postMessage(new Float32Array(input[0]));
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
