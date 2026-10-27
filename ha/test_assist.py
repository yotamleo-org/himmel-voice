"""Tests for assist.py against a stub Home Assistant websocket (HIMMEL-7779).

Run: python3 -I ha/test_assist.py
"""
import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def read_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def read_frame(conn):
    b1, b2 = read_exact(conn, 2)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", read_exact(conn, 2))[0]
    elif n == 127:
        n = struct.unpack("!Q", read_exact(conn, 8))[0]
    mask = read_exact(conn, 4)
    data = read_exact(conn, n)
    return json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def send_frame(conn, msg):
    data = json.dumps(msg).encode()
    if len(data) < 126:
        head = struct.pack("!BB", 0x81, len(data))
    else:
        head = struct.pack("!BBH", 0x81, 126, len(data))
    conn.sendall(head + data)


class StubHA:
    """Answers auth and the entity registry calls; keeps the registry in memory."""

    def __init__(self, registry=None, pipelines=None):
        self.registry = registry or {}
        self.pipelines = [dict(p) for p in pipelines or []]
        self.counters = []
        self.automations = {}
        self.calls = []
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(5)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.session, args=(conn,), daemon=True).start()

    def session(self, conn):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                head += conn.recv(1)
            if not head.startswith(b"GET /api/websocket"):
                self.rest(conn, head)
                return
            key = next(line.split(b": ")[1] for line in head.split(b"\r\n") if line.lower().startswith(b"sec-websocket-key"))
            accept = base64.b64encode(hashlib.sha1(key + GUID).digest())
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            send_frame(conn, {"type": "auth_required"})
            read_frame(conn)
            send_frame(conn, {"type": "auth_ok"})
            while True:
                msg = read_frame(conn)
                self.calls.append(msg["type"])
                send_frame(conn, {"id": msg["id"], "type": "result", "success": True, "result": self.handle(msg)})
        except (ConnectionError, OSError):
            conn.close()

    def rest(self, conn, head):
        """The automation config endpoint: POST stores, DELETE removes (404 when absent)."""
        lines = head.decode().split("\r\n")
        method, path, _ = lines[0].split(" ")
        headers = {k.lower(): v for k, v in (line.split(": ", 1) for line in lines[1:] if ": " in line)}
        body = b""
        while len(body) < int(headers.get("content-length", 0)):
            body += conn.recv(4096)
        assert headers.get("authorization") == "Bearer test-token"
        self.calls.append(f"{method} {path}")
        auto_id = path.rsplit("/", 1)[1]
        status = "200 OK"
        if method == "POST":
            self.automations[auto_id] = json.loads(body)
        elif self.automations.pop(auto_id, None) is None:
            status = "404 Not Found"
        reply = b'{"result": "ok"}'
        conn.sendall(f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nConnection: close\r\n"
                     f"Content-Length: {len(reply)}\r\n\r\n".encode() + reply)
        conn.close()

    def handle(self, msg):
        if msg["type"] == "counter/list":
            return [dict(c) for c in self.counters]
        if msg["type"] == "counter/create":
            self.counters.append({"id": msg["name"], **{k: v for k, v in msg.items() if k not in ("id", "type")}})
            return self.counters[-1]
        if msg["type"] == "counter/delete":
            self.counters = [c for c in self.counters if c["id"] != msg["counter_id"]]
            return None
        if msg["type"] == "assist_pipeline/pipeline/list":
            return {"pipelines": [dict(p) for p in self.pipelines], "preferred_pipeline": self.pipelines[0]["id"]}
        if msg["type"] == "assist_pipeline/pipeline/create":
            self.pipelines.append({"id": f"p{len(self.pipelines) + 1}", **{k: v for k, v in msg.items() if k not in ("id", "type")}})
            return self.pipelines[-1]
        if msg["type"] == "assist_pipeline/pipeline/update":
            fields = {k: v for k, v in msg.items() if k not in ("id", "type", "pipeline_id")}
            next(p for p in self.pipelines if p["id"] == msg["pipeline_id"]).update(fields)
            return None
        if msg["type"] == "assist_pipeline/pipeline/delete":
            self.pipelines = [p for p in self.pipelines if p["id"] != msg["pipeline_id"]]
            return None
        if msg["type"] == "config/entity_registry/list":  # partial entries: no aliases, as in HA
            return [{"entity_id": e} for e in self.registry]
        if msg["type"] == "config/entity_registry/get_entries":
            return {e: dict(self.registry[e]) if e in self.registry else None for e in msg["entity_ids"]}
        if msg["type"] == "config/entity_registry/update":
            self.registry[msg["entity_id"]]["aliases"] = msg["aliases"]
            return {"entity_entry": self.registry[msg["entity_id"]]}
        raise AssertionError(f"unexpected call {msg['type']}")


def run_assist(stub, *args):
    env = {**os.environ, "HA_URL": f"http://127.0.0.1:{stub.port}", "HA_TOKEN": "test-token"}
    return subprocess.run([sys.executable, "-I", os.path.join(HERE, "assist.py"), *args],
                          env=env, capture_output=True, text=True, timeout=30)


class AliasesTest(unittest.TestCase):
    def setUp(self):
        self.stub = StubHA({
            "fan.bedroom_purifier": {"entity_id": "fan.bedroom_purifier", "aliases": []},
            "fan.study_purifier": {"entity_id": "fan.study_purifier", "aliases": ["old name"]},
            "light.office": {"entity_id": "light.office", "aliases": ["lamp"]},
        })
        self.addCleanup(self.stub.server.close)
        fd, self.path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write("# entity_id: spoken name, spoken name\n"
                    "fan.bedroom_purifier: air purifier, purifier, bedroom purifier  # comment\n"
                    "fan.study_purifier: study purifier\n"
                    "fan.missing: ghost\n")
        self.addCleanup(os.unlink, self.path)

    def test_dry_run_reports_missing_and_writes_nothing(self):
        r = run_assist(self.stub, "aliases", self.path, "--dry-run")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("fan.bedroom_purifier", r.stdout)
        self.assertIn("unknown entity, skipped: fan.missing", r.stdout)
        self.assertEqual(self.stub.registry["fan.bedroom_purifier"]["aliases"], [])
        self.assertNotIn("config/entity_registry/update", self.stub.calls)

    def test_apply_sets_aliases_and_audits_clean(self):
        r = run_assist(self.stub, "aliases", self.path)
        self.assertEqual(r.returncode, 1, r.stderr)  # the unknown entity is still a finding
        self.assertEqual(self.stub.registry["fan.bedroom_purifier"]["aliases"],
                         ["air purifier", "purifier", "bedroom purifier"])
        self.assertEqual(self.stub.registry["fan.study_purifier"]["aliases"], ["study purifier"])
        self.assertEqual(self.stub.registry["light.office"]["aliases"], ["lamp"])
        again = run_assist(self.stub, "aliases", self.path, "--dry-run")
        self.assertIn("missing: 0", again.stdout)

    def test_line_without_colon_is_refused_and_writes_nothing(self):
        with open(self.path, "w") as f:
            f.write("fan.bedroom_purifier air purifier\n")
        r = run_assist(self.stub, "aliases", self.path)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no colon", r.stderr)
        self.assertNotIn("config/entity_registry/update", self.stub.calls)

    def test_clean_file_is_exit_zero_after_apply(self):
        with open(self.path, "w") as f:
            f.write("fan.bedroom_purifier: air purifier\n")
        self.assertEqual(run_assist(self.stub, "aliases", self.path, "--dry-run").returncode, 1)
        self.assertEqual(run_assist(self.stub, "aliases", self.path).returncode, 0)
        self.assertEqual(run_assist(self.stub, "aliases", self.path, "--dry-run").returncode, 0)


class CapTest(unittest.TestCase):
    SETUP = ("cap", "setup", "50", "assist_satellite.example_assist_satellite",
             "select.example_assistant=Jarvis", "select.example_assistant_2=Nabu")

    def setUp(self):
        base = {"language": "en", "stt_engine": "stt.example", "tts_engine": "tts.example", "tts_voice": "voice",
                "prefer_local_intents": False}
        self.stub = StubHA(pipelines=[
            {"id": "p1", "name": "Jarvis", "conversation_engine": "conversation.jarvis", **base},
            {"id": "p2", "name": "Nabu", "conversation_engine": "conversation.nabu", **base},
        ])
        self.addCleanup(self.stub.server.close)

    def names(self):
        return sorted(p["name"] for p in self.stub.pipelines)

    def test_dry_run_prints_payloads_and_writes_nothing(self):
        r = run_assist(self.stub, *self.SETUP, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        for auto_id in ("himmel_cap_count", "himmel_cap_switch", "himmel_cap_reset"):
            self.assertIn(auto_id, r.stdout)
        self.assertEqual((self.stub.automations, self.stub.counters, len(self.stub.pipelines)), ({}, [], 2))
        self.assertFalse([c for c in self.stub.calls if c.endswith("/create") or c.startswith("POST")])

    def test_setup_creates_counter_local_pipeline_and_three_automations(self):
        r = run_assist(self.stub, *self.SETUP)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("test-token", r.stdout + r.stderr)
        self.assertEqual(sorted(self.stub.automations), ["himmel_cap_count", "himmel_cap_reset", "himmel_cap_switch"])
        count = json.dumps(self.stub.automations["himmel_cap_count"])
        self.assertIn("conversation.jarvis", count)
        self.assertIn("conversation.nabu", count)
        self.assertNotIn("conversation.home_assistant", count)
        switch = self.stub.automations["himmel_cap_switch"]["actions"][0]
        self.assertEqual(switch["target"]["entity_id"], ["select.example_assistant", "select.example_assistant_2"])
        self.assertIn('"above": 50', json.dumps(self.stub.automations["himmel_cap_switch"]))
        reset = [(a["target"]["entity_id"], a["data"]["option"]) for a in self.stub.automations["himmel_cap_reset"]["actions"]
                 if a["action"] == "select.select_option"]
        self.assertEqual(reset, [("select.example_assistant", "Jarvis"), ("select.example_assistant_2", "Nabu")])
        self.assertEqual([c["name"] for c in self.stub.counters], ["himmel_cap"])
        local = next(p for p in self.stub.pipelines if p["name"] == "himmel-cap-local")
        self.assertEqual((local["conversation_engine"], local["prefer_local_intents"], local["tts_voice"]),
                         ("conversation.home_assistant", True, "voice"))

    def test_rerun_overwrites_in_place(self):
        self.assertEqual(run_assist(self.stub, *self.SETUP).returncode, 0)
        again = ("cap", "setup", "80", *self.SETUP[3:])
        self.assertEqual(run_assist(self.stub, *again).returncode, 0)
        self.assertEqual(len(self.stub.automations), 3)
        self.assertEqual(len(self.stub.counters), 1)
        self.assertEqual(self.names(), ["Jarvis", "Nabu", "himmel-cap-local"])
        self.assertIn('"above": 80', json.dumps(self.stub.automations["himmel_cap_switch"]))

    def test_remove_deletes_everything_and_is_repeatable(self):
        self.assertEqual(run_assist(self.stub, *self.SETUP).returncode, 0)
        self.assertEqual(run_assist(self.stub, "cap", "remove", "--dry-run").returncode, 0)
        self.assertEqual(len(self.stub.automations), 3)
        self.assertEqual(run_assist(self.stub, "cap", "remove").returncode, 0)
        self.assertEqual((self.stub.automations, self.stub.counters, self.names()), ({}, [], ["Jarvis", "Nabu"]))
        self.assertEqual(run_assist(self.stub, "cap", "remove").returncode, 0)


if __name__ == "__main__":
    unittest.main()
