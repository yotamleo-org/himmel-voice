"""Speak a sentence with Piper, transcribe it with Whisper, print timings and the transcript.

Usage: python3 roundtrip.py [host] ["sentence"] [runs] [stt_port]
Exits 0 when every transcript contains the sentence's words (case and punctuation ignored).
"""
import json
import re
import socket
import sys
import time


def send(sock, etype, data=None, payload=b""):
    header = {"type": etype, "version": "1.5.0"}
    body = json.dumps(data or {}).encode()
    header["data_length"] = len(body)
    if payload:
        header["payload_length"] = len(payload)
    sock.sendall(json.dumps(header).encode() + b"\n" + body + payload)


def read_exact(f, n):
    buf = b""
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            raise EOFError("connection closed")
        buf += chunk
    return buf


def recv(f):
    line = f.readline()
    if not line:
        raise EOFError("connection closed")
    header = json.loads(line)
    data = header.get("data") or {}
    if header.get("data_length"):
        data.update(json.loads(read_exact(f, header["data_length"])))
    payload = read_exact(f, header["payload_length"]) if header.get("payload_length") else b""
    return header["type"], data, payload


def synthesize(host, text):
    with socket.create_connection((host, 10200), timeout=60) as s:
        f = s.makefile("rb")
        t0 = time.monotonic()
        send(s, "synthesize", {"text": text})
        fmt, audio, first = None, b"", None
        while True:
            etype, data, payload = recv(f)
            if etype == "audio-start":
                fmt = data
            elif etype == "audio-chunk":
                if first is None:
                    first = time.monotonic() - t0
                audio += payload
            elif etype == "audio-stop":
                return fmt, audio, first, time.monotonic() - t0


def transcribe(host, fmt, audio, port):
    with socket.create_connection((host, port), timeout=120) as s:
        f = s.makefile("rb")
        spec = {k: fmt[k] for k in ("rate", "width", "channels")}
        send(s, "transcribe", {"language": "en"})
        send(s, "audio-start", spec)
        step = spec["rate"] * spec["width"] * spec["channels"] // 10
        for i in range(0, len(audio), step):
            send(s, "audio-chunk", spec, audio[i:i + step])
        t0 = time.monotonic()
        send(s, "audio-stop")
        while True:
            etype, data, _ = recv(f)
            if etype == "transcript":
                return data.get("text", ""), time.monotonic() - t0


def words(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).split()


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    text = sys.argv[2] if len(sys.argv) > 2 else "Turn on the kitchen light"
    runs = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    stt_port = int(sys.argv[4]) if len(sys.argv) > 4 else 10300
    if runs < 1:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    ok = True
    for n in range(runs):
        fmt, audio, tts_first, tts_total = synthesize(host, text)
        transcript, stt = transcribe(host, fmt, audio, stt_port)
        hit = all(w in words(transcript) for w in words(text))
        ok = ok and hit
        print(f"run={n} audio_s={len(audio) / (fmt['rate'] * fmt['width'] * fmt['channels']):.2f} "
              f"tts_first_s={tts_first:.3f} tts_total_s={tts_total:.3f} stt_after_stop_s={stt:.3f} "
              f"match={'yes' if hit else 'no'} transcript={transcript!r}")
    sys.exit(0 if ok else 1)


main()
