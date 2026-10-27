"""voice-gateway: the voice brains save notes to the luna inbox, search them and reach the chief (HIMMEL-7734, HIMMEL-7735).

Usage:
  himmel-secrets exec --keys VOICE_GATEWAY_TOKEN -- python3 -I gateway.py

Routes, POST only, each behind `Authorization: Bearer <VOICE_GATEWAY_TOKEN>`:
  /note    {"title", "body", "brain"} creates <vault>/00-Inbox/voice/<date>-<brain>-<slug>.md
           and nothing else: never an edit, never a delete, never a new directory.
  /search  {"q"} returns up to 5 {path, title, snippet}, read-only, from the search dirs only.
  /chief   {"satellite", "text", "chief"?} appends one `- HH:MM [voice satellite=<id> UNVERIFIED-SPEAKER] <text>`
           line to the inbox of an armed chief and nothing else: never a create, truncate or rename.
           The named chief, else the single armed one; none or several armed = 409, nothing written.

Environment:
  VOICE_GATEWAY_TOKEN        the bearer token, at least 32 characters (keyring, never a file)
  VOICE_GATEWAY_VAULT        the luna vault directory
  VOICE_GATEWAY_BIND         IPv4 address to bind (default 127.0.0.1)
  VOICE_GATEWAY_PORT         default 8740
  VOICE_GATEWAY_SEARCH_DIRS  colon-separated vault subdirectories /search reads (default
                             00-Inbox/voice). What it returns goes to cloud LLMs.
  VOICE_GATEWAY_BRIDGE_ROOT  the himmel bridge state dir whose consoles/ holds the chief inboxes
                             (default ~/.claude/handover/bridge)
  HA_HOST                    the Home Assistant host; required when the bind is not loopback.
                             Only HA_HOST and the bind address itself may connect.
Exit: 2 on a configuration error.
"""
import datetime
import heapq
import hmac
import ipaddress
import json
import os
import re
import stat
import sys
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 64 * 1024
MAX_READ = 256 * 1024
INBOX = os.path.join("00-Inbox", "voice")
BRAIN = re.compile(r"[a-z][a-z0-9-]{0,23}")
PATHLIKE = re.compile(r"^[/\\]|(^|[/\\])\.\.([/\\]|$)|\x00")
NOTE_NAME = re.compile(r"\d{4}-\d{2}-\d{2}-[a-z][a-z0-9-]{0,23}-[a-z0-9-]{1,60}(-\d{1,2})?\.md")
CHIEF_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}")
SATELLITE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
WAIT = re.compile(r"hb=(\d+) pid=\d+ key=\S+ tick=(?:ok|fail|-) state=(waiting|sampling|exited)(?: exit=(\S+))?")
LIVE_MAX_AGE = 300  # a chief mid-turn has no fresh heartbeat but still reads its inbox: busy
BUSY_MAX_AGE = 1800  # past this a chief is treated as released or dead
CHIEF_TEXT_MAX = 500
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
        search_dirs.append(os.path.relpath(real, vault))
    bridge_root = os.path.realpath(env.get("VOICE_GATEWAY_BRIDGE_ROOT")
                                   or os.path.join(os.path.expanduser("~"), ".claude", "handover", "bridge"))
    peers = {str(bind)} | ({str(ha_host)} if ha_host else set())
    # Held for the process lifetime: a vault or ancestor swapped for a link later changes nothing.
    vault_fd = os.open(vault, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    return {"token": token, "vault": vault, "vault_fd": vault_fd, "bind": str(bind), "port": port,
            "peers": peers, "search_dirs": search_dirs, "bridge_root": bridge_root}


def today():
    return datetime.date.today().isoformat()


def slugify(title):
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60].rstrip("-") or "note"


def open_dir(vault_fd, rel):
    """Open rel below the vault fd, refusing a symlink at every component; None if any is one.

    A component swapped for a link after a startup check cannot redirect what happens below it."""
    dfd = os.dup(vault_fd)
    for part in rel.split(os.sep):
        if part in ("", "."):
            continue
        try:
            nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
        except OSError:
            nfd = None
        os.close(dfd)
        if nfd is None:
            return None
        dfd = nfd
    return dfd


def write_note(vault_fd, title, body, brain):
    """Create one inbox file; return its vault-relative path, or None if the inbox is not confined."""
    created = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    text = (f"---\ntitle: {json.dumps(title, ensure_ascii=False)}\nbrain: {brain}\n"
            f"created: {created}\nsource: voice-gateway\ntags: [voice]\n---\n\n{body.rstrip()}\n")
    data = text.encode("utf-8")  # raises before any file exists, e.g. on a lone surrogate
    stem = f"{today()}-{brain}-{slugify(title)}"
    dfd = open_dir(vault_fd, INBOX)
    if dfd is None:
        return None
    try:
        for n in range(1, 100):
            name = f"{stem}.md" if n == 1 else f"{stem}-{n}.md"
            if not NOTE_NAME.fullmatch(name):  # a bare file name, never a path
                raise ValueError(f"not a note name: {name!r}")
            path = os.path.normpath(os.path.join(INBOX, name))
            if not path.startswith(INBOX + os.sep):
                raise ValueError(f"not a note name: {name!r}")
            leaf = os.path.basename(path)
            try:
                fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o644, dir_fd=dfd)
            except FileExistsError:
                continue
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
            except OSError:
                os.unlink(leaf, dir_fd=dfd)  # never leave an empty or truncated note behind
                raise
            return f"00-Inbox/voice/{leaf}"
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


def read_note(dfd, name):
    """Read a regular file under dfd without following a symlink; None if it is not one."""
    try:
        # O_NONBLOCK: opening a FIFO must not hang the worker before the regular-file check.
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        return f.read(MAX_READ).decode("utf-8", errors="replace"), st.st_mtime


def scan(rootfd, rel, terms, seen):
    """Yield a ranked item per matching note below rootfd; fwalk follows no symlink and opens by fd."""
    for d, dirs, files, dfd in os.fwalk(".", dir_fd=rootfd, follow_symlinks=False):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for name in files:
            p = os.path.normpath(os.path.join(rel, d, name))
            if not name.endswith(".md") or p in seen:
                continue
            seen.add(p)
            got = read_note(dfd, name)
            if got is None:
                continue
            text, mtime = got
            low = text.lower()
            matched = [t for t in terms if t in low]
            if matched:
                yield ((len(matched), sum(low.count(t) for t in matched), mtime), len(seen), p, text, matched)


def search(cfg, q):
    terms = list(dict.fromkeys(t for t in re.findall(r"\w+", q.lower()) if len(t) >= 3))[:8]
    best, seen = [], set()
    if not terms:
        return []
    for rel in cfg["search_dirs"]:
        rootfd = open_dir(cfg["vault_fd"], rel)
        if rootfd is None:
            continue
        try:
            for item in scan(rootfd, rel, terms, seen):
                if len(best) < RESULTS:
                    heapq.heappush(best, item)
                else:
                    heapq.heappushpop(best, item)
        finally:
            os.close(rootfd)
    out = []
    for _, _, p, text, matched in sorted(best, reverse=True):
        title, body = split_note(text, os.path.splitext(os.path.basename(p))[0])
        at = max(body.lower().find(matched[0]), 0)
        start = max(at - SNIPPET // 3, 0)
        snippet = " ".join(body[start:start + SNIPPET].split())
        out.append({"path": p.replace(os.sep, "/"), "title": title, "snippet": snippet})
    return out


def fold(text):
    """One line, length-capped: a line break would let the text start a line of its own."""
    one = re.sub(r"[\x00-\x1f\x7f]", " ", " ⏎ ".join(text.strip().splitlines())).strip()
    return one if len(one) <= CHIEF_TEXT_MAX else one[:CHIEF_TEXT_MAX - 1] + "…"


def armed_chiefs(dfd, now):
    """{name: busy} for each chief with a regular-file inbox and a live waiter heartbeat.

    The same test as chief-route.ts armedConsoles, over the consoles dir fd."""
    armed = {}
    for entry in sorted(os.listdir(dfd)):
        name = entry[:-8]
        if not entry.endswith(".md.wait") or not CHIEF_NAME.fullmatch(name) or ".." in name:
            continue
        raw = read_note(dfd, entry)
        m = WAIT.fullmatch(raw[0].strip()) if raw is not None else None
        if not m:
            continue
        age = now - int(m.group(1))
        exited = m.group(2) == "exited"
        if age < 0 or age >= BUSY_MAX_AGE or (exited and not (m.group(3) or "").startswith("wake-")):
            continue
        try:
            if not stat.S_ISREG(os.stat(f"{name}.md", dir_fd=dfd, follow_symlinks=False).st_mode):
                continue
        except OSError:
            continue
        armed[name] = exited or age >= LIVE_MAX_AGE
    return armed


def append_chief(root, satellite, text, chief):
    """Append one tagged line to an armed chief's inbox; return (status, body).

    Opens O_APPEND with no O_CREAT, so an inbox deleted a moment ago is never recreated."""
    try:
        dfd = os.open(os.path.join(root, "consoles"), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        dfd = None
    try:
        armed = armed_chiefs(dfd, time.time()) if dfd is not None else {}
        if chief is not None:
            target = next((n for n in armed if n == chief), None)  # the listing's own name, never the request's
            miss = f"no armed chief named {chief}"
        else:
            target = next(iter(armed)) if len(armed) == 1 else None
            miss = f"{len(armed)} chiefs are armed; name one" if armed else "no chief is armed"
        if target is None:
            return 409, {"error": miss}
        line = f"- {datetime.datetime.now().strftime('%H:%M')} [voice satellite={satellite} UNVERIFIED-SPEAKER] {fold(text)}\n"
        data = line.encode("utf-8")  # raises before any write, e.g. on a lone surrogate
        try:
            fd = os.open(f"{target}.md", os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=dfd)
        except OSError:
            return 409, {"error": f"chief {target} is not listening"}
        with os.fdopen(fd, "ab") as f:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return 409, {"error": f"chief {target} is not listening"}
            f.write(data)
        return 200, {"chief": target, "busy": armed[target]}
    finally:
        if dfd is not None:
            os.close(dfd)


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
        if route not in ("/note", "/search", "/chief"):
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
                    and title.strip() and len(title) <= 200 and BRAIN.fullmatch(brain)
                    and not PATHLIKE.search(title)):
                self.reply(400, {"error": "title (no path), body and brain ([a-z0-9-]) are required"})
                return
            try:
                path = write_note(self.cfg["vault_fd"], title.strip(), body, brain)
            except UnicodeEncodeError:
                self.reply(400, {"error": "title and body must be valid text"})
                return
            except (OSError, ValueError):
                self.reply(500, {"error": "the note could not be written"})
                return
            if path is None:
                self.reply(403, {"error": "the inbox is not a plain directory inside the vault"})
                return
            self.reply(201, {"path": path})
        elif route == "/chief":
            satellite, text, chief = data.get("satellite"), data.get("text"), data.get("chief")
            if not (isinstance(satellite, str) and SATELLITE.fullmatch(satellite) and ".." not in satellite
                    and isinstance(text, str) and text.strip() and len(text) <= MAX_BODY
                    and (chief is None or (isinstance(chief, str) and CHIEF_NAME.fullmatch(chief)
                                           and ".." not in chief))):
                self.reply(400, {"error": "satellite, text and an optional plain chief name are required"})
                return
            try:
                status, out = append_chief(self.cfg["bridge_root"], satellite, text, chief)
            except UnicodeEncodeError:
                self.reply(400, {"error": "text must be valid text"})
                return
            except OSError:
                self.reply(500, {"error": "the chief inbox could not be written"})
                return
            self.reply(status, out)
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
