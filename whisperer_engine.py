"""Transcription engines with one interface: faster-whisper (Windows, CPU) and mlx-whisper (Mac, Apple GPU).

    model = load(repo)
    duration, segments = transcribe(model, media_path, language)   # segments: iterator of Segment
"""
import importlib
import os
import queue
import re
import threading
from collections import namedtuple

import whisperer_core as core

Segment = namedtuple("Segment", "start end text")
SAMPLE_RATE = 16000
CPU_THREADS = 14  # faster-whisper on the user's i5-13600KF (14 cores); see docs/windows-packaging.md


def language_codes():
    if core.ENGINE == "mlx":
        from mlx_whisper.tokenizer import LANGUAGES
        return set(LANGUAGES)
    from faster_whisper.tokenizer import _LANGUAGE_CODES
    return set(_LANGUAGE_CODES)


def load(repo):
    if core.ENGINE == "mlx":
        return _mlx_load(repo)
    from faster_whisper import WhisperModel
    return WhisperModel(repo, device="cpu", compute_type="int8", cpu_threads=CPU_THREADS)


def transcribe(model, media, lang):
    """Returns (duration in seconds, iterator of Segment); segments arrive while transcription runs."""
    if core.ENGINE == "mlx":
        return _mlx_transcribe(model, media, lang)
    segments, info = model.transcribe(str(media), language=lang, vad_filter=True)
    return info.duration, segments


def decode_audio(path, sr=SAMPLE_RATE):
    """Any audio/video file -> mono float32 numpy array at 16 kHz, via PyAV (no ffmpeg install needed)."""
    import av
    import numpy as np

    resampler = av.AudioResampler(format="s16", layout="mono", rate=sr)
    chunks = []
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            chunks.extend(r.to_ndarray() for r in resampler.resample(frame))
        chunks.extend(r.to_ndarray() for r in resampler.resample(None))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks, axis=1).reshape(-1).astype(np.float32) / 32768.0


# ---------- mlx-whisper ----------
# mlx-whisper returns all segments only at the end, but with verbose=True it prints each one as soon as it is
# decoded: "[mm:ss.mmm --> mm:ss.mmm] text". The module's print is replaced to turn those lines into Segments.
RE_MLX_LINE = re.compile(r"^\[([\d:.]+) --> ([\d:.]+)\] ?(.*)$", re.S)


def _seconds(ts):
    secs = 0.0
    for part in ts.split(":"):
        secs = secs * 60 + float(part)
    return secs


def _mlx_module():
    # not `from mlx_whisper import transcribe`: the package rebinds that name to the function of the same name
    return importlib.import_module("mlx_whisper.transcribe")


def _mlx_load(repo):
    import mlx.core as mx

    mlx_transcribe = _mlx_module()
    if os.environ.get("WHISPERER_MLX_DEVICE") == "cpu":  # CI machines have no Metal GPU
        mx.set_default_device(mx.cpu)
    mlx_transcribe.ModelHolder.get_model(repo, mx.float16)  # loads (and downloads) once; reused below
    return repo


def _mlx_transcribe(repo, media, lang):
    mlx_transcribe = _mlx_module()
    audio = decode_audio(media)
    duration = len(audio) / SAMPLE_RATE
    q = queue.Queue()

    def capture(*args, **kwargs):
        text = " ".join(str(a) for a in args)
        m = RE_MLX_LINE.match(text)
        if m:
            start, end = _seconds(m[1]), _seconds(m[2])
            if end > start and m[3].strip():
                q.put(Segment(start, end, m[3]))
        else:
            print(*args, **kwargs)  # e.g. "Detected language: Ukrainian"

    def run():
        try:
            mlx_transcribe.print = capture
            mlx_transcribe.transcribe(audio, path_or_hf_repo=repo, language=lang, verbose=True)
            q.put(None)
        except BaseException as e:
            q.put(e)

    threading.Thread(target=run, daemon=True).start()

    def segments():
        while (item := q.get()) is not None:
            if isinstance(item, BaseException):
                raise item
            yield item

    return duration, segments()
