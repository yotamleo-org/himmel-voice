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
        self.scramble = False  # HA releases keep aliases in a set, so the order is not the written one
        self.pipelines = [dict(p) for p in pipelines or []]
        self.counters = []
        self.input_numbers = []
        self.dashboards = []
        self.dashboard_configs = {}
        self.services = []
        self.entities = []  # entity ids get_states reports besides the helpers
        self.fail_services = False
        self.absent_status = "404 Not Found"  # HA answers a DELETE of an absent automation with 400 "Resource not found"
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
        if path.startswith("/api/services/"):
            if self.fail_services:
                status = "404 Not Found"
            else:
                self.services.append((path, json.loads(body)))
        elif method == "POST":
            self.automations[auto_id] = json.loads(body)
        elif self.automations.pop(auto_id, None) is None:
            status = self.absent_status
        reply = b'{"message": "Resource not found"}' if status != "200 OK" else b'{"result": "ok"}'
        conn.sendall(f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nConnection: close\r\n"
                     f"Content-Length: {len(reply)}\r\n\r\n".encode() + reply)
        conn.close()

    def handle(self, msg):
        if msg["type"] == "get_states":
            figure = next((str(b["value"]) for p, b in reversed(self.services) if p.endswith("set_value")), "0.0")
            return ([{"entity_id": e, "state": "idle"} for e in self.entities] +
                    [{"entity_id": f"input_number.{i['name']}", "state": figure} for i in self.input_numbers])
        if msg["type"] == "counter/list":
            return [dict(c) for c in self.counters]
        if msg["type"] == "counter/create":
            self.counters.append({"id": msg["name"], **{k: v for k, v in msg.items() if k not in ("id", "type")}})
            return self.counters[-1]
        if msg["type"] == "counter/delete":
            self.counters = [c for c in self.counters if c["id"] != msg["counter_id"]]
            return None
        if msg["type"] == "input_number/list":
            return [dict(i) for i in self.input_numbers]
        if msg["type"] == "input_number/create":
            self.input_numbers.append({"id": msg["name"], **{k: v for k, v in msg.items() if k not in ("id", "type")}})
            return self.input_numbers[-1]
        if msg["type"] == "input_number/delete":
            self.input_numbers = [i for i in self.input_numbers if i["id"] != msg["input_number_id"]]
            return None
        if msg["type"] == "lovelace/dashboards/list":
            return [dict(d) for d in self.dashboards]
        if msg["type"] == "lovelace/dashboards/create":
            self.dashboards.append({"id": msg["url_path"].replace("-", "_"),
                                    **{k: v for k, v in msg.items() if k not in ("id", "type")}})
            return self.dashboards[-1]
        if msg["type"] == "lovelace/dashboards/delete":
            self.dashboards = [d for d in self.dashboards if d["id"] != msg["dashboard_id"]]
            self.dashboard_configs = {}
            return None
        if msg["type"] == "lovelace/config/save":
            self.dashboard_configs[msg["url_path"]] = msg["config"]
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
        # the own-name marker stays first, so the entity's own name stays the primary name
        self.assertEqual(self.stub.registry["fan.bedroom_purifier"]["aliases"], [None, "air purifier"])
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

    def test_duplicate_entity_in_another_case_is_refused_and_writes_nothing(self):
        with open(self.path, "w") as f:
            f.write("FAN.bedroom_purifier: air purifier\nfan.bedroom_purifier: purifier\n")
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


class CapTest(unittest.TestCase):
    SETUP = ("cap", "setup", "10", "assist_satellite.example_assist_satellite",
             "select.example_assistant=Jarvis", "select.example_assistant_2=Nabu")

    def setUp(self):
        base = {"language": "en", "stt_engine": "stt.example", "tts_engine": "tts.example", "tts_voice": "voice",
                "prefer_local_intents": False}
        self.stub = StubHA(pipelines=[
            {"id": "p1", "name": "Jarvis", "conversation_engine": "conversation.jarvis", **base},
            {"id": "p2", "name": "Nabu", "conversation_engine": "conversation.nabu", **base},
        ])
        self.stub.entities = ["assist_satellite.example_assist_satellite", "select.example_assistant",
                              "select.example_assistant_2", "conversation.jarvis", "conversation.nabu"]
        self.addCleanup(self.stub.server.close)

    def names(self):
        return sorted(p["name"] for p in self.stub.pipelines)

    def test_dry_run_prints_payloads_and_writes_nothing(self):
        r = run_assist(self.stub, *self.SETUP, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        for auto_id in ("himmel_cap_count", "himmel_cap_switch", "himmel_cap_reset"):
            self.assertIn(auto_id, r.stdout)
        for needle in ("himmel_cap_usd_per_request", "himmel-voice-cap", "$10.00"):
            self.assertIn(needle, r.stdout)
        self.assertEqual((self.stub.automations, self.stub.counters, len(self.stub.pipelines)), ({}, [], 2))
        self.assertEqual((self.stub.input_numbers, self.stub.dashboards, self.stub.services), ([], [], []))
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
        trigger = self.stub.automations["himmel_cap_switch"]["triggers"][0]
        self.assertEqual(trigger["trigger"], "template")
        self.assertIn("counter.himmel_cap", trigger["value_template"])
        self.assertIn("input_number.himmel_cap_usd_per_request", trigger["value_template"])
        self.assertIn(">= 10", trigger["value_template"])
        self.assertEqual([i["name"] for i in self.stub.input_numbers], ["himmel_cap_usd_per_request"])
        self.assertEqual(self.stub.services, [("/api/services/input_number/set_value",
                                               {"entity_id": "input_number.himmel_cap_usd_per_request", "value": 0.03})])
        self.assertEqual([d["url_path"] for d in self.stub.dashboards], ["himmel-voice-cap"])
        card = self.stub.dashboard_configs["himmel-voice-cap"]["views"][0]["cards"][0]
        self.assertEqual(card["type"], "markdown")
        for needle in ("counter.himmel_cap", "input_number.himmel_cap_usd_per_request", "$10.00", "himmel-cap-local",
                       "select.example_assistant", "select.example_assistant_2", "timedelta(days=1)"):
            self.assertIn(needle, card["content"])
        reset = [(a["target"]["entity_id"], a["data"]["option"]) for a in self.stub.automations["himmel_cap_reset"]["actions"]
                 if a["action"] == "select.select_option"]
        self.assertEqual(reset, [("select.example_assistant", "Jarvis"), ("select.example_assistant_2", "Nabu")])
        self.assertEqual([c["name"] for c in self.stub.counters], ["himmel_cap"])
        local = next(p for p in self.stub.pipelines if p["name"] == "himmel-cap-local")
        self.assertEqual((local["conversation_engine"], local["prefer_local_intents"], local["tts_voice"]),
                         ("conversation.home_assistant", True, "voice"))

    def test_rerun_overwrites_in_place(self):
        self.assertEqual(run_assist(self.stub, *self.SETUP).returncode, 0)
        again = ("cap", "setup", "12.5", *self.SETUP[3:])
        self.assertEqual(run_assist(self.stub, *again).returncode, 0)
        self.assertEqual(len(self.stub.automations), 3)
        self.assertEqual((len(self.stub.counters), len(self.stub.input_numbers), len(self.stub.dashboards)), (1, 1, 1))
        self.assertEqual(self.names(), ["Jarvis", "Nabu", "himmel-cap-local"])
        self.assertIn(">= 12.5", self.stub.automations["himmel_cap_switch"]["triggers"][0]["value_template"])
        self.assertIn("$12.50", self.stub.dashboard_configs["himmel-voice-cap"]["views"][0]["cards"][0]["content"])
        self.assertEqual(len(self.stub.services), 1)  # the per-request figure is set once, so an edit in HA survives a re-run

    def test_unknown_entity_is_refused_before_any_write(self):
        for flag in ("--dry-run", None):
            args = [*self.SETUP[:-1], "select.example_typo=Nabu"] + ([flag] if flag else [])
            r = run_assist(self.stub, *args)
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("unknown entities: select.example_typo", r.stderr)
        self.assertEqual((self.stub.automations, self.stub.counters, len(self.stub.pipelines)), ({}, [], 2))

    def test_a_failed_write_stops_setup_and_a_rerun_sets_the_figure(self):
        self.stub.fail_services = True
        r = run_assist(self.stub, *self.SETUP)
        self.assertEqual(r.returncode, 2)
        self.assertIn("HTTP 404", r.stderr)
        self.assertNotIn("done", r.stdout)
        self.stub.fail_services = False
        self.assertEqual(run_assist(self.stub, *self.SETUP).returncode, 0)
        self.assertEqual(len(self.stub.services), 1)  # the figure was still 0 after the failure, so the rerun set it
        self.assertEqual(len(self.stub.input_numbers), 1)
        self.assertEqual(run_assist(self.stub, *self.SETUP).returncode, 0)
        self.assertEqual(len(self.stub.services), 1)  # now it is set, an edit in HA survives

    def test_remove_is_repeatable_when_ha_answers_400_resource_not_found(self):
        self.stub.absent_status = "400 Bad Request"
        self.assertEqual(run_assist(self.stub, *self.SETUP).returncode, 0)
        self.assertEqual(run_assist(self.stub, "cap", "remove").returncode, 0)
        again = run_assist(self.stub, "cap", "remove")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("absent automation himmel_cap_count", again.stdout)

    def test_remove_deletes_everything_and_is_repeatable(self):
        self.assertEqual(run_assist(self.stub, *self.SETUP).returncode, 0)
        self.assertEqual(run_assist(self.stub, "cap", "remove", "--dry-run").returncode, 0)
        self.assertEqual(len(self.stub.automations), 3)
        self.assertEqual(run_assist(self.stub, "cap", "remove").returncode, 0)
        self.assertEqual((self.stub.automations, self.stub.counters, self.names()), ({}, [], ["Jarvis", "Nabu"]))
        self.assertEqual((self.stub.input_numbers, self.stub.dashboards), ([], []))
        self.assertEqual(run_assist(self.stub, "cap", "remove").returncode, 0)


if __name__ == "__main__":
    unittest.main()
