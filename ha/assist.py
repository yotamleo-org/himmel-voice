"""Configure Home Assistant Assist: entity exposure and the voice pipelines (HIMMEL-7731).

Usage:
  python3 -I assist.py audit
  python3 -I assist.py list
  python3 -I assist.py apply <allowlist file>
  python3 -I assist.py aliases <aliases file> [--dry-run]
  python3 -I assist.py cap setup <daily cap in USD> <satellite entity> <pipeline select entity>=<pipeline name>... [--dry-run]
  python3 -I assist.py cap remove [--dry-run]
  python3 -I assist.py pipelines
  python3 -I assist.py satellite
  python3 -I assist.py debug <pipeline name>
  python3 -I assist.py voice <pipeline name> <tts voice>

`list` prints every entity, by domain, marking the ones the deny list refuses.
`apply` turns "expose new entities" off for Assist, exposes exactly the allowlisted
entities, unexposes every other one, then audits. A denied entity is never exposed,
even when allowlisted. `aliases` sets each listed entity's Assist aliases (one
`entity_id: name, name` per line) to exactly that list, then re-reads and counts what is
still missing; `--dry-run` prints the change and writes nothing. `cap setup` caps the
LLM voice agents at a daily USD estimate (HIMMEL-7780; HA reads no usage data, so spend is
the call counter times an editable USD-per-request input_number): a counter, that
input_number, a local-only pipeline, a dashboard card, and three
automations (ids himmel_cap_*, overwritten in place on a re-run) that count each call to
the named pipelines' conversation agents, once the estimate reaches the cap switch every named pipeline select
(the BOX-3 has one per wake word) to the local-only pipeline with a spoken notice, and at
midnight reset and select each one's pipeline again. The automations go through HA's REST config endpoint (the
UI editor's call); `cap remove` deletes all of it. `pipelines` prints each assistant's engines and voice, `debug`
the events of a pipeline's latest run, `voice` sets a pipeline's text-to-speech voice.

HA_URL (http://host:8123) and HA_TOKEN come from the environment, never from argv,
and the token is never printed.
Exit: 0 clean, 1 an audit finding or a refused allowlist entry, 2 a usage, connection or response error.
"""
import base64
import json
import os
import re
import socket
import ssl
import struct
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

ASSISTANT = "conversation"
CAP_COUNTER = "himmel_cap"
CAP_PIPELINE = "himmel-cap-local"
CAP_USD_INPUT = "himmel_cap_usd_per_request"
CAP_DASHBOARD = "himmel-voice-cap"
USD_PER_REQUEST = 0.03
CAP_AUTOMATIONS = ("himmel_cap_count", "himmel_cap_switch", "himmel_cap_reset")
CAP_NOTICE = "Daily AI limit reached. Local commands only until midnight."
LOCAL_ENGINE = "conversation.home_assistant"
DENY_DOMAINS = ("lock", "alarm_control_panel")
DENY_WORDS = re.compile(r"grow|tent|ggs|bluelab|guardian|bucket|heater|heizung|heizlüfter|towel.?warmer", re.IGNORECASE)


class WS:
    def __init__(self, url):
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ConnectionError("HA_URL must look like http://host:8123 or https://host")
        port = u.port or (443 if u.scheme == "https" else 80)
        sock = socket.create_connection((u.hostname, port), timeout=30)
        if u.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
        self.sock = sock
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET /api/websocket HTTP/1.1\r\nHost: {u.hostname}:{port}\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(1)
            if not chunk:
                raise ConnectionError("handshake closed")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.next_id = 1

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("connection closed")
            buf += chunk
        return buf

    def _send_frame(self, opcode, data):
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, msg):
        self._send_frame(0x1, json.dumps(msg).encode())

    def recv(self):
        message = b""
        while True:
            b1, b2 = self._read(2)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            data = self._read(n)
            if opcode == 0x9:
                self._send_frame(0xA, data)
            elif opcode == 0x8:
                raise ConnectionError("server closed the connection")
            elif opcode in (0x0, 0x1):
                message += data
                if b1 & 0x80:
                    return json.loads(message)

    def call(self, msg_type, **data):
        msg_id = self.next_id
        self.next_id += 1
        self.send({"id": msg_id, "type": msg_type, **data})
        while True:
            reply = self.recv()
            if reply.get("id") == msg_id and reply.get("type") == "result":
                if not reply.get("success"):
                    raise RuntimeError(f"{msg_type}: {reply.get('error')}")
                return reply.get("result")


def fail(msg):
    print(f"assist: {msg}", file=sys.stderr)
    sys.exit(2)


def connect():
    url, token = os.environ.get("HA_URL"), os.environ.get("HA_TOKEN")
    if not url or not token:
        fail("set HA_URL and HA_TOKEN in the environment")
    ws = WS(url)
    ws.base, ws.token = url.rstrip("/"), token
    if ws.recv().get("type") != "auth_required":
        fail("unexpected greeting")
    ws.send({"type": "auth", "access_token": token})
    if ws.recv().get("type") != "auth_ok":
        fail("authentication failed")
    return ws


def denied(entity_id, name):
    return entity_id.split(".", 1)[0] in DENY_DOMAINS or bool(DENY_WORDS.search(f"{entity_id} {name}"))


def snapshot(ws):
    names = {s["entity_id"]: s.get("attributes", {}).get("friendly_name", "") for s in ws.call("get_states")}
    listed = ws.call("homeassistant/expose_entity/list")["exposed_entities"]
    exposed = sorted(e for e, a in listed.items() if a.get(ASSISTANT))
    expose_new = ws.call("homeassistant/expose_new_entities/get", assistant=ASSISTANT)["expose_new"]
    return names, exposed, expose_new


def audit(ws):
    names, exposed, expose_new = snapshot(ws)
    findings = 0
    print(f"expose new entities: {'ON' if expose_new else 'off'}")
    if expose_new:
        findings += 1
    print(f"exposed to Assist: {len(exposed)}")
    for e in exposed:
        name = names.get(e, "")
        if denied(e, name):
            tag, findings = "DENIED ", findings + 1
        elif e.startswith("switch."):
            tag = "CHECK  "
        else:
            tag = "ok     "
        print(f"  {tag}{e}  {name}")
    print(f"findings: {findings}")
    return findings


def list_entities(ws):
    names, exposed, _ = snapshot(ws)
    for e in sorted(names, key=lambda e: (e.split(".", 1)[0], e)):
        tag = "DENIED " if denied(e, names[e]) else "exposed" if e in exposed else "       "
        print(f"{tag} {e}  {names[e]}")
    return 0


def apply(ws, path):
    with open(path, encoding="utf-8") as f:
        wanted = {line.split("#", 1)[0].strip() for line in f} - {""}
    names, exposed, _ = snapshot(ws)
    refused = sorted(e for e in wanted if denied(e, names.get(e, "")))
    unknown = sorted(e for e in wanted if e not in names)
    for e in refused:
        print(f"refused (deny list): {e}")
    for e in unknown:
        print(f"unknown entity, skipped: {e}")
    allow = sorted(wanted - set(refused) - set(unknown))
    ws.call("homeassistant/expose_new_entities/set", assistant=ASSISTANT, expose_new=False)
    others = sorted((set(names) | set(exposed)) - set(allow))
    if others:
        ws.call("homeassistant/expose_entity", assistants=[ASSISTANT], entity_ids=others, should_expose=False)
    if allow:
        ws.call("homeassistant/expose_entity", assistants=[ASSISTANT], entity_ids=allow, should_expose=True)
    print(f"applied: {len(allow)} exposed, {len(others)} unexposed")
    return audit(ws) + len(refused)


def aliases(ws, path, dry_run):
    wanted = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                entity, colon, names = line.partition(":")
                if not colon:
                    raise ValueError(f"aliases file: no colon in line {line!r}")
                entity = entity.strip().lower()  # HA entity ids are lowercase
                if entity in wanted:
                    raise ValueError(f"aliases file: duplicate entity {entity!r}")
                wanted[entity] = list(dict.fromkeys(n.strip() for n in names.split(",") if n.strip()))

    def named(aliases):
        # None in HA's list means "also answer to the entity's own name": not a spoken alias
        return [a for a in aliases if a is not None]

    def differing(current):
        # HA may return aliases in set order, so compare as sets
        return {e: n for e, n in wanted.items() if e in current and set(named(current[e])) != set(n)}

    def registry():
        # the registry list call omits aliases; only full entries carry them
        entries = ws.call("config/entity_registry/get_entries", entity_ids=sorted(wanted))
        return {e: (entry.get("aliases") or []) for e, entry in entries.items() if entry}

    current = registry()
    unknown = sorted(e for e in wanted if e not in current)
    for e in unknown:
        print(f"unknown entity, skipped: {e}")
    todo = differing(current)
    for e in sorted(todo):
        print(f"  {'would set' if dry_run else 'set'} {e}: {named(current[e])} -> {todo[e]}")
    if not dry_run:
        for e, n in sorted(todo.items()):
            # keep the own-name marker where it was (first): alias order sets the primary name
            marker = [None] if None in current[e] else []
            ws.call("config/entity_registry/update", entity_id=e,
                    aliases=marker + n if current[e][:1] == [None] else n + marker)
        current = registry()
        todo = differing(current)
    print(f"missing: {len(todo)}")
    return len(todo) + len(unknown)


def pipelines(ws):
    res = ws.call("assist_pipeline/pipeline/list")
    for p in res["pipelines"]:
        star = "*" if p["id"] == res.get("preferred_pipeline") else " "
        print(f"{star} {p['name']}  id={p['id']}")
        for k in ("language", "conversation_engine", "prefer_local_intents", "stt_engine",
                  "stt_language", "tts_engine", "tts_language", "tts_voice", "wake_word_entity", "wake_word_id"):
            print(f"    {k}: {p.get(k)}")
    return 0


def find_pipeline(ws, name):
    matches = [p for p in ws.call("assist_pipeline/pipeline/list")["pipelines"] if p["name"].lower() == name.lower()]
    if len(matches) != 1:
        fail(f"{len(matches)} pipelines named {name!r}")
    return matches[0]


def debug(ws, name):
    p = find_pipeline(ws, name)
    runs = ws.call("assist_pipeline/pipeline_debug/list", pipeline_id=p["id"])["pipeline_runs"]
    if not runs:
        print(f"{p['name']}: no runs recorded")
        return 1
    run = runs[-1]
    print(f"{p['name']}: run {run['pipeline_run_id']} at {run.get('timestamp')}")
    for ev in ws.call("assist_pipeline/pipeline_debug/get", pipeline_id=p["id"],
                      pipeline_run_id=run["pipeline_run_id"])["events"]:
        data = ev.get("data") or {}
        if ev["type"] == "stt-end":
            data = {"text": (data.get("stt_output") or {}).get("text")}
        elif ev["type"] == "intent-start":
            data = {k: data.get(k) for k in ("engine", "language", "prefer_local_intents")}
        elif ev["type"] == "intent-end":
            response = (data.get("intent_output") or {}).get("response") or {}
            data = {"processed_locally": data.get("processed_locally"),
                    "response_type": response.get("response_type"),
                    "speech": ((response.get("speech") or {}).get("plain") or {}).get("speech")}
        elif ev["type"] == "tts-start":
            data = {k: data.get(k) for k in ("engine", "voice", "language")}
        elif ev["type"] == "tts-end":
            data = {}
        print(f"  {ev.get('timestamp')} {ev['type']} {json.dumps(data, ensure_ascii=False)[:400]}")
    return 0


def voice(ws, name, tts_voice):
    p = find_pipeline(ws, name)
    fields = {k: v for k, v in p.items() if k != "id"}
    fields["tts_voice"] = tts_voice
    ws.call("assist_pipeline/pipeline/update", pipeline_id=p["id"], **fields)
    print(f"{p['name']}: tts_voice {p.get('tts_voice')} -> {find_pipeline(ws, name).get('tts_voice')}")
    return 0


def rest(ws, method, path, body=None):
    """HA's REST config call (the one the UI editor uses); returns the status, 404 included."""
    req = urllib.request.Request(ws.base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {ws.token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return 404
        raise RuntimeError(f"{method} {path}: HTTP {err.code}") from None


def cap_spend_template():
    return f"states('counter.{CAP_COUNTER}') | float(0) * states('input_number.{CAP_USD_INPUT}') | float(0)"


def cap_card(cap, selects):
    # no usage data is reachable from HA, so spend is the request count times a configurable USD figure
    content = (
        "Requests today: **{{ states('counter.@C@') | int(0) }}**\n\n"
        "Estimated spend: **${{ (@SPEND@) | round(2) }}** of $@CAP@ "
        "(at ${{ states('input_number.@U@') }} per request)\n\n"
        "Capped: **{{ 'yes' if @SELECTS@ | select('is_state', '@P@') | list | length > 0 else 'no' }}**\n\n"
        "Resets: **{{ (now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).strftime('%a %H:%M') }}**")
    for key, value in (("@C@", CAP_COUNTER), ("@SPEND@", cap_spend_template()), ("@CAP@", f"{cap:.2f}"),
                       ("@U@", CAP_USD_INPUT), ("@SELECTS@", json.dumps(sorted(selects))), ("@P@", CAP_PIPELINE)):
        content = content.replace(key, value)
    return {"views": [{"title": "Voice cap", "cards": [{"type": "markdown", "title": "Voice agent daily cap", "content": content}]}]}


def cap_automations(cap, agents, sat, selects):
    counter = f"counter.{CAP_COUNTER}"
    return {
        "himmel_cap_count": {
            "alias": "Voice cap: count agent calls", "mode": "queued", "conditions": [],
            "triggers": [{"trigger": "state", "entity_id": agents}],
            "actions": [{"action": "counter.increment", "target": {"entity_id": counter}}]},
        "himmel_cap_switch": {
            "alias": "Voice cap: switch to local-only", "mode": "single", "conditions": [],
            "triggers": [{"trigger": "template", "value_template": f"{{{{ {cap_spend_template()} >= {cap:g} }}}}"}],
            "actions": [{"action": "select.select_option", "target": {"entity_id": sorted(selects)}, "data": {"option": CAP_PIPELINE}},
                        {"action": "assist_satellite.announce", "target": {"entity_id": sat}, "data": {"message": CAP_NOTICE}}]},
        "himmel_cap_reset": {
            "alias": "Voice cap: midnight reset", "mode": "single", "conditions": [],
            "triggers": [{"trigger": "time", "at": "00:00:00"}],
            "actions": [{"action": "counter.reset", "target": {"entity_id": counter}}] +
                       [{"action": "select.select_option", "target": {"entity_id": s}, "data": {"option": name}}
                        for s, name in sorted(selects.items())]},
    }


def cap_setup(ws, cap, sat, selects, dry_run):
    normal = {s: find_pipeline(ws, name) for s, name in selects.items()}
    agents = sorted({p["conversation_engine"] for p in normal.values()})
    local = {k: v for k, v in normal[sorted(normal)[0]].items() if k != "id"}
    local.update(name=CAP_PIPELINE, conversation_engine=LOCAL_ENGINE, prefer_local_intents=True)
    existing = next((p for p in ws.call("assist_pipeline/pipeline/list")["pipelines"] if p["name"] == CAP_PIPELINE), None)
    counter = {"name": CAP_COUNTER, "initial": 0, "step": 1, "restore": True}
    have_counter = any(c["name"] == CAP_COUNTER for c in ws.call("counter/list"))
    usd = {"name": CAP_USD_INPUT, "min": 0, "max": 5, "step": 0.001, "mode": "box", "unit_of_measurement": "USD"}
    have_usd = any(i["name"] == CAP_USD_INPUT for i in ws.call("input_number/list"))
    dashboard = {"url_path": CAP_DASHBOARD, "title": "Voice cap", "icon": "mdi:cash-clock", "show_in_sidebar": True,
                 "require_admin": False}
    have_dashboard = any(d["url_path"] == CAP_DASHBOARD for d in ws.call("lovelace/dashboards/list"))
    card = cap_card(cap, selects)
    verb = "would" if dry_run else "will"
    print(f"{verb} {'update' if existing else 'create'} pipeline {CAP_PIPELINE}: {json.dumps(local, sort_keys=True)}")
    print(f"{verb} {'keep' if have_counter else 'create'} counter: {json.dumps(counter)}")
    print(f"{verb} {'keep' if have_usd else 'create'} input_number: {json.dumps(usd)}"
          f"{'' if have_usd else f' and set it to {USD_PER_REQUEST} once'}")
    print(f"{verb} {'keep' if have_dashboard else 'create'} dashboard: {json.dumps(dashboard)}")
    print(f"{verb} save dashboard {CAP_DASHBOARD} config: {json.dumps(card)}")
    automations = cap_automations(cap, agents, sat, selects)
    for auto_id, payload in automations.items():
        print(f"{verb} set automation {auto_id}: {json.dumps(payload, sort_keys=True)}")
    if dry_run:
        return 0
    if existing:
        ws.call("assist_pipeline/pipeline/update", pipeline_id=existing["id"], **local)
    else:
        ws.call("assist_pipeline/pipeline/create", **local)
    if not have_counter:
        ws.call("counter/create", **counter)
    if not have_usd:
        ws.call("input_number/create", **usd)
        rest(ws, "POST", "/api/services/input_number/set_value",
             {"entity_id": f"input_number.{CAP_USD_INPUT}", "value": USD_PER_REQUEST})
    if not have_dashboard:
        ws.call("lovelace/dashboards/create", **dashboard)
    ws.call("lovelace/config/save", url_path=CAP_DASHBOARD, config=card)
    for auto_id, payload in automations.items():
        rest(ws, "POST", f"/api/config/automation/config/{auto_id}", payload)
        print(f"set automation {auto_id}")
    print("done; confirm in Developer tools that one voice request changes each agent entity's state once")
    return 0


def cap_remove(ws, dry_run):
    verb = "would delete" if dry_run else "deleted"
    for auto_id in CAP_AUTOMATIONS:
        if dry_run:
            print(f"would delete automation {auto_id}")
        else:
            print(f"{'deleted' if rest(ws, 'DELETE', f'/api/config/automation/config/{auto_id}') != 404 else 'absent'} automation {auto_id}")
    for c in (c for c in ws.call("counter/list") if c["name"] == CAP_COUNTER):
        if not dry_run:
            ws.call("counter/delete", counter_id=c["id"])
        print(f"{verb} counter {CAP_COUNTER}")
    for i in (i for i in ws.call("input_number/list") if i["name"] == CAP_USD_INPUT):
        if not dry_run:
            ws.call("input_number/delete", input_number_id=i["id"])
        print(f"{verb} input_number {CAP_USD_INPUT}")
    for d in (d for d in ws.call("lovelace/dashboards/list") if d["url_path"] == CAP_DASHBOARD):
        if not dry_run:
            ws.call("lovelace/dashboards/delete", dashboard_id=d["id"])
        print(f"{verb} dashboard {CAP_DASHBOARD}")
    for p in (p for p in ws.call("assist_pipeline/pipeline/list")["pipelines"] if p["name"] == CAP_PIPELINE):
        if not dry_run:
            ws.call("assist_pipeline/pipeline/delete", pipeline_id=p["id"])
        print(f"{verb} pipeline {CAP_PIPELINE}")
    return 0


def cap_command(args):
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    pairs = [a.partition("=") for a in args[3:]]
    if (len(args) >= 4 and args[0] == "setup" and re.fullmatch(r"\d+(\.\d+)?", args[1]) and float(args[1]) > 0
            and all(s and eq and name for s, eq, name in pairs)):
        return lambda ws: cap_setup(ws, float(args[1]), args[2], {s: name for s, _, name in pairs}, dry_run)
    if args == ["remove"]:
        return lambda ws: cap_remove(ws, dry_run)
    return None


def satellite(ws):
    states = ws.call("get_states")
    for sat in (s["entity_id"] for s in states if s["entity_id"].startswith("assist_satellite.")):
        print(f"{sat}: {next(s['state'] for s in states if s['entity_id'] == sat)}")
        cfg = ws.call("assist_satellite/get_configuration", entity_id=sat)
        words = {w["id"]: w.get("wake_word") for w in cfg.get("available_wake_words", [])}
        print(f"    active wake words: {[words.get(w, w) for w in cfg.get('active_wake_words', [])]}"
              f" (max {cfg.get('max_active_wake_words')}), pipeline entity: {cfg.get('pipeline_entity_id')}")
        device = sat.split(".", 1)[1].removesuffix("_assist_satellite")
        for s in states:
            domain, obj = s["entity_id"].split(".", 1)
            if domain in ("select", "switch") and obj.startswith(device):
                print(f"    {s['entity_id']} = {s['state']}  options={s.get('attributes', {}).get('options')}")
    return 0


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "audit":
        cmd = lambda ws: audit(ws)
    elif len(sys.argv) == 2 and sys.argv[1] == "list":
        cmd = lambda ws: list_entities(ws)
    elif len(sys.argv) == 3 and sys.argv[1] == "apply":
        cmd = lambda ws: apply(ws, sys.argv[2])
    elif len(sys.argv) in (3, 4) and sys.argv[1] == "aliases" and sys.argv[3:] in ([], ["--dry-run"]):
        cmd = lambda ws: aliases(ws, sys.argv[2], len(sys.argv) == 4)
    elif sys.argv[1:2] == ["cap"] and cap_command(sys.argv[2:]):
        cmd = cap_command(sys.argv[2:])
    elif len(sys.argv) == 2 and sys.argv[1] == "pipelines":
        cmd = lambda ws: pipelines(ws)
    elif len(sys.argv) == 2 and sys.argv[1] == "satellite":
        cmd = lambda ws: satellite(ws)
    elif len(sys.argv) == 3 and sys.argv[1] == "debug":
        cmd = lambda ws: debug(ws, sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == "voice":
        cmd = lambda ws: voice(ws, sys.argv[2], sys.argv[3])
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    try:
        rc = cmd(connect())
    except (OSError, ConnectionError, RuntimeError, ValueError, KeyError, TypeError) as err:
        fail(err)
    sys.exit(1 if rc else 0)


main()
