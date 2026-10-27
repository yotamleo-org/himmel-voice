"""voice-gateway: the voice brains save notes to the luna inbox and search them (HIMMEL-7734).

Usage:
  himmel-secrets exec --keys VOICE_GATEWAY_TOKEN -- python3 -I gateway.py

Routes, POST only, each behind `Authorization: Bearer <VOICE_GATEWAY_TOKEN>`:
  /note    {"title", "body", "brain"} creates <vault>/00-Inbox/voice/<date>-<brain>-<slug>.md
           and nothing else: never an edit, never a delete, never a new directory.
  /search  {"q"} returns up to 5 {path, title, snippet}, read-only, from the search dirs only.

Environment:
  VOICE_GATEWAY_TOKEN        the bearer token, at least 32 characters (keyring, never a file)
  VOICE_GATEWAY_VAULT        the luna vault directory
  VOICE_GATEWAY_BIND         IPv4 address to bind (default 127.0.0.1)
  VOICE_GATEWAY_PORT         default 8740
  VOICE_GATEWAY_SEARCH_DIRS  colon-separated vault subdirectories /search reads (default
                             00-Inbox/voice). What it returns goes to cloud LLMs.
  HA_HOST                    the Home Assistant host; required when the bind is not loopback.
                             Only HA_HOST and the bind address itself may connect.
Exit: 2 on a configuration error.
"""
import datetime
import hmac
import ipaddress
import json
import os
import re
import sys
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 64 * 1024
MAX_READ = 256 * 1024
INBOX = os.path.join("00-Inbox", "voice")
BRAIN = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
PATHLIKE = re.compile(r"^[/\\]|(^|[/\\])\.\.([/\\]|$)|\x00")
RESULTS = 5
SNIPPET = 600


def die(msg):
    print(f"gateway.py: {msg}", file=sys.stderr)
    sys.exit(2)


def inside(path, root):
    return path == root or path.startswith(root + os.sep)


def load_config(env):
    token = env.get("VOICE_GATEWAY_TOKEN", "")
    if len(token) < 32:
        die("VOICE_GATEWAY_TOKEN must be set, at least 32 characters")
    vault = os.path.realpath(env.get("VOICE_GATEWAY_VAULT") or die("set VOICE_GATEWAY_VAULT"))
    if not os.path.isdir(vault):
        die("VOICE_GATEWAY_VAULT is not a directory")
    try:
        bind = ipaddress.IPv4Address(env.get("VOICE_GATEWAY_BIND") or "127.0.0.1")
        port = int(env.get("VOICE_GATEWAY_PORT") or 8740)
        ha_host = ipaddress.IPv4Address(env["HA_HOST"]) if env.get("HA_HOST") else None
    except ValueError as e:
        die(f"bad address or port: {e}")
    if not 0 <= port <= 65535:
        die("VOICE_GATEWAY_PORT out of range")
    if ha_host is None and not bind.is_loopback:
        die("a LAN bind needs HA_HOST")
    search_dirs = []
    for rel in (env.get("VOICE_GATEWAY_SEARCH_DIRS") or INBOX).split(":"):
        if not rel or os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
            die(f"search dir {rel!r} must be a relative path inside the vault")
        real = os.path.realpath(os.path.join(vault, rel))
        if not inside(real, vault) or not os.path.isdir(real):
            die(f"search dir {rel!r} is not a directory inside the vault")
        search_dirs.append(real)
    peers = {str(bind)} | ({str(ha_host)} if ha_host else set())
    return {"token": token, "vault": vault, "bind": str(bind), "port": port,
            "peers": peers, "search_dirs": search_dirs}


def today():
    return datetime.date.today().isoformat()


def slugify(title):
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60].rstrip("-") or "note"


def write_note(vault, title, body, brain):
    """Create one inbox file; return its vault-relative path, or None if the inbox is not confined."""
    inbox = os.path.join(vault, INBOX)
    if os.path.realpath(inbox) != inbox or not os.path.isdir(inbox):
        return None
    created = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    text = (f"---\ntitle: {json.dumps(title, ensure_ascii=False)}\nbrain: {brain}\n"
            f"created: {created}\nsource: voice-gateway\ntags: [voice]\n---\n\n{body.rstrip()}\n")
    stem = f"{today()}-{brain}-{slugify(title)}"
    dfd = os.open(inbox, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for n in range(1, 100):
            name = f"{stem}.md" if n == 1 else f"{stem}-{n}.md"
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o644, dir_fd=dfd)
            except FileExistsError:
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            return f"00-Inbox/voice/{name}"
    finally:
        os.close(dfd)
    raise FileExistsError(stem)


def split_note(text, fallback):
    title, body = fallback, text
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            for line in text[4:end].splitlines():
                if line.startswith("title:"):
                    raw = line[6:].strip()
                    try:
                        title = json.loads(raw) if raw.startswith('"') else raw or fallback
                    except ValueError:
                        title = raw
            body = text[end + 4:]
    return str(title), body


def search(cfg, q):
    terms = list(dict.fromkeys(t for t in re.findall(r"\w+", q.lower()) if len(t) >= 3))[:8]
    hits, seen = [], set()
    for root in cfg["search_dirs"]:
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith(".") and not os.path.islink(os.path.join(d, x))]
            for name in files:
                p = os.path.join(d, name)
                if not name.endswith(".md") or p in seen or os.path.islink(p):
                    continue
                seen.add(p)
                try:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        text = f.read(MAX_READ)
                    mtime = os.path.getmtime(p)
                except OSError:
                    continue
                low = text.lower()
                matched = [t for t in terms if t in low]
                if matched:
                    hits.append(((len(matched), sum(low.count(t) for t in matched), mtime), p, text, matched))
    hits.sort(key=lambda h: h[0], reverse=True)
    out = []
    for _, p, text, matched in hits[:RESULTS]:
        title, body = split_note(text, os.path.splitext(os.path.basename(p))[0])
        at = max(body.lower().find(matched[0]), 0)
        start = max(at - SNIPPET // 3, 0)
        snippet = " ".join(body[start:start + SNIPPET].split())
        out.append({"path": os.path.relpath(p, cfg["vault"]).replace(os.sep, "/"), "title": title,
                    "snippet": snippet})
    return out


class Handler(BaseHTTPRequestHandler):
    timeout = 15
    cfg = None

    def reply(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def gate(self):
        """Refuse a foreign peer, a bad token or an unknown route; return the route or None."""
        if self.client_address[0] not in self.cfg["peers"]:
            self.reply(403, {"error": "forbidden"})
            return None
        want = f"Bearer {self.cfg['token']}".encode()
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), want):
            self.reply(401, {"error": "unauthorized"})
            return None
        route = self.path.split("?", 1)[0]
        if route not in ("/note", "/search"):
            self.reply(404, {"error": "not found"})
            return None
        if self.command != "POST":
            self.reply(405, {"error": "POST only"})
            return None
        return route

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", ""))
            if length < 0:
                raise ValueError
        except ValueError:
            self.reply(411, {"error": "Content-Length required"})
            return None
        if length > MAX_BODY:
            self.reply(413, {"error": "too large"})
            return None
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self.reply(400, {"error": "a JSON object is required"})
            return None
        return data

    def do_POST(self):
        route = self.gate()
        data = route and self.read_json()
        if data is None:
            return
        if route == "/note":
            title, body, brain = data.get("title"), data.get("body"), data.get("brain")
            if not (isinstance(title, str) and isinstance(body, str) and isinstance(brain, str)
                    and title.strip() and len(title) <= 200 and BRAIN.match(brain)
                    and not PATHLIKE.search(title)):
                self.reply(400, {"error": "title (no path), body and brain ([a-z0-9-]) are required"})
                return
            path = write_note(self.cfg["vault"], title.strip(), body, brain)
            if path is None:
                self.reply(403, {"error": "the inbox is not a plain directory inside the vault"})
                return
            self.reply(201, {"path": path})
        else:
            q = data.get("q")
            if not isinstance(q, str) or not q.strip() or len(q) > 200:
                self.reply(400, {"error": "q is required, at most 200 characters"})
                return
            self.reply(200, {"results": search(self.cfg, q)})

    do_GET = do_PUT = do_DELETE = do_PATCH = do_POST


def make_server(cfg):
    handler = type("BoundHandler", (Handler,), {"cfg": cfg})
    server = ThreadingHTTPServer((cfg["bind"], cfg["port"]), handler)
    server.daemon_threads = True
    return server


def main():
    cfg = load_config(os.environ)
    server = make_server(cfg)
    print(f"voice-gateway on {cfg['bind']}:{server.server_address[1]}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
