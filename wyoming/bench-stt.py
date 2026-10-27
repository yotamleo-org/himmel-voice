#!/usr/bin/env python3
"""Time faster-whisper on short clips (HIMMEL-7834).

usage: bench-stt.py MODEL DEVICE COMPUTE_TYPE CLIP.wav [CLIP.wav ...]
e.g.   bench-stt.py large-v3-turbo cuda float16 a.wav b.wav
The first clip is transcribed once untimed to warm the model; each clip is then timed.
"""
import sys
import time

from faster_whisper import WhisperModel


def main(argv):
    model_name, device, compute, *clips = argv
    if not clips:
        sys.exit(__doc__)
    t0 = time.perf_counter()
    model = WhisperModel(model_name, device=device, compute_type=compute)
    print(f"{model_name} {device} {compute} load={time.perf_counter() - t0:.2f}s")
    list(model.transcribe(clips[0], language="en", beam_size=1)[0])
    for clip in clips:
        t0 = time.perf_counter()
        segments, _ = model.transcribe(clip, language="en", beam_size=1)
        text = "".join(s.text for s in segments).strip()
        print(f"{clip}\t{time.perf_counter() - t0:.2f}s\t{text}")


if __name__ == "__main__":
    main(sys.argv[1:])
