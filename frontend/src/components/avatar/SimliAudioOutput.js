// Only instantiated by VoiceAvatarPage. No microphone capture or speech generation.
export const CAPTURE_WORKLET = `
class AvatarPcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.epoch = 0;
    this.samples = [];
    this.port.onmessage = ({ data }) => {
      this.epoch = data.epoch;
      this.samples = [];
    };
  }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (input) {
      for (const value of input) {
        this.samples.push(value);
        if (this.samples.length === 320) {
          const bytes = new Uint8Array(640);
          const view = new DataView(bytes.buffer);
          this.samples.forEach((sample, index) => {
            const value = Math.max(-1, Math.min(1, sample));
            view.setInt16(index * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
          });
          this.port.postMessage({ epoch: this.epoch, bytes }, [bytes.buffer]);
          this.samples = [];
        }
      }
    }
    return true;
  }
}
registerProcessor('avatar-pcm-capture', AvatarPcmCapture);
`;

// Area-average downsampling: three 24 kHz input samples -> two 16 kHz samples.
// Retain incomplete groups across provider chunks so no samples/time are lost.
export function createPcmResampler() {
  let tail = [];
  return {
    reset() { tail = []; },
    convert(base64) {
      const binary = atob(base64);
      if (binary.length % 2) throw new Error('Invalid PCM16 audio chunk');
      const samples = tail.slice();
      for (let i = 0; i < binary.length; i += 2) {
        const value = binary.charCodeAt(i) | (binary.charCodeAt(i + 1) << 8);
        samples.push(value >= 32768 ? value - 65536 : value);
      }
      const groups = Math.floor(samples.length / 3);
      const bytes = new Uint8Array(groups * 4);
      const view = new DataView(bytes.buffer);
      for (let group = 0; group < groups; group++) {
        const [a, b, c] = samples.slice(group * 3, group * 3 + 3);
        view.setInt16(group * 4, Math.round((2 * a + b) / 3), true);
        view.setInt16(group * 4 + 2, Math.round((b + 2 * c) / 3), true);
      }
      tail = samples.slice(groups * 3);
      return bytes;
    },
  };
}

export function createSimliAudioOutput() {
  let client = null;
  let audio = null;
  let context = null;
  let capture = null;
  let source = null;
  let silentGain = null;
  let active = false;
  let accepting = false;
  let epoch = 0;
  const resampler = createPcmResampler();

  function interrupt() {
    accepting = false;
    epoch++;
    resampler.reset();
    capture?.port.postMessage({ epoch });
    if (audio) audio.muted = true;
    try { client?.ClearBuffer(); } catch { /* session already closed */ }
  }

  function reset() {
    active = false;
    interrupt();
    source?.disconnect();
    capture?.disconnect();
    silentGain?.disconnect();
    if (capture) capture.port.onmessage = null;
    if (context) void context.close().catch(() => {});
    source = capture = silentGain = context = null;
  }

  function beginResponse() {
    if (!active || !client || !audio) return;
    if (!accepting) {
      epoch++;
      capture?.port.postMessage({ epoch });
    }
    accepting = true;
    audio.muted = false;
  }

  function send(bytes) {
    if (!active || !accepting || !client || !bytes.length) return;
    try { client.sendAudioData(bytes); }
    catch { reset(); } // Never fall back to a second, unsynchronized player.
  }

  return {
    attach(nextClient, audioElement) {
      reset();
      client = nextClient;
      audio = audioElement;
      audio.muted = true;
    },
    detach() {
      reset();
      client = audio = null;
    },
    async prepare() {
      reset();
      if (!client || !audio) throw new Error('Wait for the avatar to connect, then start the conversation.');
      const currentEpoch = epoch;
      const currentAudio = audio;
      const ctx = new AudioContext({ sampleRate: 16000 });
      context = ctx;
      // Called directly from Start Conversation: unlock the returned Simli audio.
      currentAudio.muted = false;
      try {
        await Promise.all([ctx.resume(), currentAudio.play()]);
        const url = URL.createObjectURL(new Blob([CAPTURE_WORKLET], { type: 'application/javascript' }));
        try { await ctx.audioWorklet.addModule(url); }
        finally { URL.revokeObjectURL(url); }
        if (context !== ctx || currentEpoch !== epoch) throw new Error('Avatar session was stopped.');
        capture = new AudioWorkletNode(ctx, 'avatar-pcm-capture');
        capture.port.onmessage = ({ data }) => {
          if (data.epoch === epoch) send(data.bytes);
        };
        // A zero-gain sink keeps capture processing active without local playback.
        silentGain = ctx.createGain();
        silentGain.gain.value = 0;
        capture.connect(silentGain);
        silentGain.connect(ctx.destination);
        active = true;
        currentAudio.muted = true;
      } catch (error) {
        if (context === ctx) reset();
        throw error;
      }
    },
    connectStream(stream) {
      if (!active || !context || !capture) return;
      source?.disconnect();
      source = context.createMediaStreamSource(stream);
      source.connect(capture);
      // OpenAI's output_audio_buffer.started enables each response, not the
      // continuous remote track (which also contains silence between turns).
    },
    sendPcm(base64) {
      if (!active || !client) return;
      beginResponse();
      send(resampler.convert(base64));
    },
    beginResponse,
    interrupt,
    reset,
  };
}
