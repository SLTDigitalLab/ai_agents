import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { createPcmResampler, createSimliAudioOutput, CAPTURE_WORKLET } from './SimliAudioOutput.js';

function pcm(samples) {
  const bytes = Buffer.alloc(samples.length * 2);
  samples.forEach((sample, i) => bytes.writeInt16LE(sample, i * 2));
  return bytes.toString('base64');
}
function setup() {
  const nodes = [];
  class Node {
    constructor() {
      this.connections = [];
      this.port = { onmessage: null, messages: [], postMessage: value => this.port.messages.push(value) };
      nodes.push(this);
    }
    connect(target) { this.connections.push(target); }
    disconnect() { this.connections = []; }
  }
  class Context {
    constructor(options) { this.options = options; this.destination = {}; this.audioWorklet = { addModule: async () => {} }; }
    resume() { return Promise.resolve(); }
    close() { this.closed = true; return Promise.resolve(); }
    createGain() { const n = new Node(); n.gain = { value: 1 }; return n; }
    createMediaStreamSource(stream) { const n = new Node(); n.stream = stream; return n; }
  }
  globalThis.AudioContext = Context;
  globalThis.AudioWorkletNode = Node;
  const packets = [];
  let clears = 0;
  const client = { sendAudioData: bytes => packets.push(bytes), ClearBuffer: () => clears++ };
  const audio = { muted: true, play: async () => {} };
  const output = createSimliAudioOutput();
  output.attach(client, audio);
  return { output, audio, packets, nodes, client, clears: () => clears };
}

test('24k PCM is converted to 16k without losing samples at chunk boundaries', () => {
  const whole = createPcmResampler().convert(pcm([300,600,900,-300,-600,-900]));
  const split = createPcmResampler();
  const parts = [split.convert(pcm([300])), split.convert(pcm([600,900,-300])), split.convert(pcm([-600,-900]))];
  assert.deepEqual(Buffer.concat(parts), Buffer.from(whole));
  assert.equal(whole.byteLength, 8);
  assert.equal(new DataView(whole.buffer).getInt16(0, true), 400);
});

test('Gemini goes only to Simli; interruption clears buffer and sample remainder', async () => {
  const {output,audio,packets,clears} = setup();
  await output.prepare();
  output.sendPcm(pcm([300,300,300,999]));
  assert.equal(packets.length,1);
  assert.equal(audio.muted,false);
  const before = clears();
  output.interrupt();
  assert.equal(audio.muted,true);
  assert.equal(clears(),before+1);
  output.sendPcm(pcm([600,600,600]));
  assert.equal(new DataView(packets[1].buffer).getInt16(0,true),600);
  output.reset();
  output.sendPcm(pcm([900,900,900]));
  assert.equal(packets.length,2);
  assert.equal(audio.muted,true);
});

test('OpenAI remote track feeds silent capture; stale worklet packets are discarded', async () => {
  const {output,audio,packets,nodes} = setup();
  await output.prepare();
  output.connectStream({id:'remote assistant audio'});
  const [capture,gain,source] = nodes;
  assert.equal(gain.gain.value,0);
  assert.deepEqual(source.connections,[capture]);
  output.beginResponse();
  const firstEpoch = capture.port.messages.at(-1).epoch;
  const bytes = new Uint8Array([1,2]);
  capture.port.onmessage({data:{epoch:firstEpoch,bytes}});
  assert.equal(packets.length,1);
  output.interrupt();
  capture.port.onmessage({data:{epoch:firstEpoch,bytes}});
  assert.equal(packets.length,1);
  output.beginResponse();
  capture.port.onmessage({data:{epoch:firstEpoch,bytes}});
  assert.equal(packets.length,1);
  capture.port.onmessage({data:{epoch:capture.port.messages.at(-1).epoch,bytes}});
  assert.equal(packets.length,2);
  output.detach();
  assert.equal(audio.muted,true);
  assert.equal(capture.port.onmessage,null);
});

test('avatar unavailable or browser autoplay blocked never enables another player', async () => {
  const {output,audio} = setup();
  audio.play = async () => { throw new Error('Autoplay blocked'); };
  await assert.rejects(output.prepare(), /Autoplay blocked/);
  assert.equal(audio.muted,true);
  output.detach();
  await assert.rejects(output.prepare(), /Wait for the avatar/);
});

test('worklet emits PCM16 little-endian and resets partial old response frames', () => {
  let Worklet;
  class Processor { constructor(){ this.port={postMessage:packet=>this.sent.push(packet)}; this.sent=[]; } }
  vm.runInNewContext(CAPTURE_WORKLET,{AudioWorkletProcessor:Processor,registerProcessor:(_name,c)=>{Worklet=c;}});
  const w = new Worklet();
  w.process([[new Float32Array(128).fill(-1)]]);
  w.port.onmessage({data:{epoch:2}});
  w.process([[new Float32Array(320).fill(1)]]);
  assert.equal(w.sent.length,1);
  assert.equal(w.sent[0].epoch,2);
  assert.equal(w.sent[0].bytes.length,640);
  assert.equal(w.sent[0].bytes[0],255);
  assert.equal(w.sent[0].bytes[1],127);
});
