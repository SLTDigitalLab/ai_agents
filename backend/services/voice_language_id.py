"""In-memory PCM language identification, independent of Gemini transcripts.

Scores are model posteriors, NOT calibrated probabilities of correctness.
The threshold requires evaluation on consented local recordings.
"""
import asyncio
import base64
import math
import threading
from array import array
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parents[1] / '.audio_lid_cache'
_classifier = None
_model_lock = threading.Lock()


def classify_pcm(pcm):
    global _classifier
    if len(pcm) < 32000:
        return {'language': None, 'confidence': None, 'method': 'insufficient_audio'}
    # Silence can receive a high softmax score even though it contains no
    # speech. Require enough audible frames before trusting any model result.
    pcm_samples = array('h', pcm)
    frame_size = 3200  # 100 ms at 16 kHz
    voiced_frames = sum(
        math.sqrt(sum(float(sample) ** 2 for sample in pcm_samples[offset:offset + frame_size])
                  / max(1, len(pcm_samples[offset:offset + frame_size]))) / 32768 > .018
        for offset in range(0, len(pcm_samples), frame_size)
    )
    if voiced_frames < 8:
        return {'language': None, 'confidence': None, 'method': 'insufficient_speech'}
    with _model_lock:
        if not all((MODEL_PATH / name).is_file() for name in (
            'hyperparams.yaml', 'embedding_model.ckpt', 'classifier.ckpt', 'label_encoder.txt'
        )):
            return {'language': None, 'confidence': None, 'method': 'model_unavailable'}
        import torch
        from speechbrain.inference.classifiers import EncoderClassifier
        from speechbrain.utils.fetching import LocalStrategy
        if _classifier is None:
            _classifier = EncoderClassifier.from_hparams(
                source=str(MODEL_PATH), savedir=str(MODEL_PATH),
                overrides={'pretrained_path': str(MODEL_PATH)},
                local_strategy=LocalStrategy.COPY,
            )
            _classifier.hparams.label_encoder.expect_len(3)
        samples = torch.frombuffer(bytearray(pcm), dtype=torch.int16).float() / 32768
        with torch.inference_mode():
            log_probs, _, _, _ = _classifier.classify_batch(samples.unsqueeze(0))
        probs = log_probs.reshape(-1).softmax(dim=0)
        scores, indexes = probs.topk(2)
        label = _classifier.hparams.label_encoder.ind2lab[int(indexes[0])].split(':')[0]
        score = float(scores[0])
        margin = float(scores[0] - scores[1])
        return {'language': label if score >= .85 and margin >= .3 else None,
                'confidence': round(score, 4), 'method': 'audio_lid'}


class AudioLanguageWindow:
    """Bound memory to six seconds; separate utterances on 0.75 s silence.

    The return value signals sustained new speech for interruption handling.
    This energy gate is only a capture aid, not a speech or language classifier.
    Echo cancellation is still required at the microphone.
    """
    def __init__(self):
        self.pcm = bytearray()
        self.silence = 0
        self.active = False
        self.voiced_seconds = 0
        self.speech_reported = False

    def feed(self, encoded):
        if not isinstance(encoded, str) or len(encoded) > 128000:
            return False
        try:
            pcm = base64.b64decode(encoded, validate=True)
            if len(pcm) % 2:
                return False
            samples = array('h', pcm)
            rms = math.sqrt(sum(float(s) ** 2 for s in samples) / max(1, len(samples))) / 32768
        except (ValueError, TypeError):
            return False
        voiced = rms > .018
        started = voiced and not self.active
        if started:
            self.pcm.clear()
            self.active = True
            self.voiced_seconds = 0
            self.speech_reported = False
        if self.active:
            self.pcm.extend(pcm)
            del self.pcm[:-192000]
        if voiced and self.active:
            self.voiced_seconds += len(pcm) / 32000
        elif not voiced:
            self.voiced_seconds = 0
        self.silence = 0 if voiced else self.silence + len(pcm) / 32000
        if self.silence >= .75:
            self.active = False
            self.voiced_seconds = 0
            self.speech_reported = False
        # A single loud packet can be fan noise or playback echo. Require
        # sustained speech before cancelling a pending filler/tool turn.
        if self.active and self.voiced_seconds >= .35 and not self.speech_reported:
            self.speech_reported = True
            return True
        return False

    def snapshot(self):
        return bytes(self.pcm)

    def clear(self):
        self.pcm.clear()
        self.active = False
        self.silence = 0
        self.voiced_seconds = 0
        self.speech_reported = False


async def identify_audio(pcm):
    try:
        return await asyncio.to_thread(classify_pcm, pcm)
    except Exception:
        # Provider/dependency details must not leak through voice diagnostics.
        return {'language': None, 'confidence': None, 'method': 'model_unavailable'}


async def warm_audio_language_model():
    """Load the optional local model before the first ambiguous voice turn.

    Only generated, constant samples are used; no microphone audio is saved.
    A missing dependency/model remains non-fatal for the voice session.
    """
    try:
        samples = array('h', [2000] * 32000).tobytes()
        await asyncio.to_thread(classify_pcm, samples)
        return True
    except Exception:
        return False
