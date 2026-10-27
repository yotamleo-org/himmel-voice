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

    def __init__(self, registry):
        self.registry = registry
        self.scramble = False  # HA releases keep aliases in a set, so the order is not the written one
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

    def handle(self, msg):
        if msg["type"] == "config/entity_registry/list":  # partial entries: no aliases, as in HA
            return [{"entity_id": e} for e in self.registry]
        if msg["type"] == "config/entity_registry/get_entries":
            return {e: {**self.registry[e], "aliases": self.registry[e]["aliases"][::-1] if self.scramble else self.registry[e]["aliases"]}
                    if e in self.registry else None for e in msg["entity_ids"]}
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

    def test_aliases_returned_out_of_order_are_not_missing_and_not_rewritten(self):
        with open(self.path, "w") as f:
            f.write("fan.bedroom_purifier: air purifier, purifier, air purifier\n")
        self.stub.scramble = True
        self.assertEqual(run_assist(self.stub, "aliases", self.path).returncode, 0)
        self.assertEqual(self.stub.registry["fan.bedroom_purifier"]["aliases"], ["air purifier", "purifier"])
        self.stub.calls.clear()
        self.assertEqual(run_assist(self.stub, "aliases", self.path).returncode, 0)
        self.assertNotIn("config/entity_registry/update", self.stub.calls)

    def test_the_entity_own_name_marker_is_kept_and_is_not_an_alias(self):
        self.stub.registry["fan.bedroom_purifier"]["aliases"] = [None]
        with open(self.path, "w") as f:
            f.write("fan.bedroom_purifier: air purifier\n")
        self.assertEqual(run_assist(self.stub, "aliases", self.path).returncode, 0)
        self.assertEqual(self.stub.registry["fan.bedroom_purifier"]["aliases"], ["air purifier", None])
        self.stub.calls.clear()
        self.assertEqual(run_assist(self.stub, "aliases", self.path).returncode, 0)
        self.assertNotIn("config/entity_registry/update", self.stub.calls)

    def test_duplicate_entity_is_refused_and_writes_nothing(self):
        with open(self.path, "w") as f:
            f.write("fan.bedroom_purifier: air purifier\nfan.bedroom_purifier: purifier\n")
        r = run_assist(self.stub, "aliases", self.path)
        self.assertEqual(r.returncode, 2)
        self.assertIn("duplicate entity", r.stderr)
        self.assertNotIn("config/entity_registry/update", self.stub.calls)

    def test_clean_file_is_exit_zero_after_apply(self):
        with open(self.path, "w") as f:
            f.write("fan.bedroom_purifier: air purifier\n")
        self.assertEqual(run_assist(self.stub, "aliases", self.path, "--dry-run").returncode, 1)
        self.assertEqual(run_assist(self.stub, "aliases", self.path).returncode, 0)
        self.assertEqual(run_assist(self.stub, "aliases", self.path, "--dry-run").returncode, 0)


if __name__ == "__main__":
    unittest.main()
