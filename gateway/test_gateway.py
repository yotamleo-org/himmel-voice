"""Tests for gateway.py against a temp vault (HIMMEL-7734).

Run: python3 -I gateway/test_gateway.py
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gateway  # noqa: E402

TOKEN = "t" * 40


class Base(unittest.TestCase):
    peers = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.vault = os.path.join(root, "vault")
        self.outside = os.path.join(root, "outside")
        self.inbox = os.path.join(self.vault, "00-Inbox", "voice")
        os.makedirs(self.inbox)
        os.makedirs(self.outside)
        self.server = None

    def start(self, **env):
        cfg = gateway.load_config({"VOICE_GATEWAY_TOKEN": TOKEN, "VOICE_GATEWAY_VAULT": self.vault,
                                   "VOICE_GATEWAY_PORT": "0", **env})
        if self.peers is not None:
            cfg["peers"] = self.peers
        self.server = gateway.make_server(cfg)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, token=TOKEN, raw=None):
        if not self.server:
            self.start()
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        conn.close()
        return out

    def files(self, d):
        return sorted(os.listdir(d))

    def write(self, rel, text):
        p = os.path.join(self.vault, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
        return p


NOTE = {"title": "Heat pumps in old houses", "body": "They work below zero.", "brain": "codex"}


class NoteTests(Base):
    def test_no_token_is_401(self):
        self.assertEqual(self.call("POST", "/note", NOTE, token=None)[0], 401)
        self.assertEqual(self.files(self.inbox), [])

    def test_wrong_token_is_401(self):
        self.assertEqual(self.call("POST", "/note", NOTE, token="x" * 40)[0], 401)
        self.assertEqual(self.files(self.inbox), [])

    def test_good_request_writes_exactly_one_inbox_file(self):
        status, out = self.call("POST", "/note", NOTE)
        self.assertEqual(status, 201)
        names = self.files(self.inbox)
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], r"^\d{4}-\d{2}-\d{2}-codex-heat-pumps-in-old-houses\.md$")
        self.assertEqual(out["path"], f"00-Inbox/voice/{names[0]}")
        text = open(os.path.join(self.inbox, names[0])).read()
        self.assertIn('title: "Heat pumps in old houses"', text)
        self.assertIn("They work below zero.", text)
        everything = [os.path.join(d, f) for d, _, fs in os.walk(self.tmp.name) for f in fs]
        self.assertEqual(everything, [os.path.join(self.inbox, names[0])])

    def test_same_title_twice_never_overwrites(self):
        self.call("POST", "/note", NOTE)
        self.assertEqual(self.call("POST", "/note", NOTE)[0], 201)
        self.assertEqual(len(self.files(self.inbox)), 2)

    def test_path_escapes_refused(self):
        for bad in ({**NOTE, "title": "../../outside/x"}, {**NOTE, "title": "/etc/passwd"},
                    {**NOTE, "title": "..\\..\\x"}, {**NOTE, "brain": "../x"},
                    {**NOTE, "brain": "/abs"}, {**NOTE, "brain": "Codex Two"}, {**NOTE, "brain": "codex\n"}):
            self.assertEqual(self.call("POST", "/note", bad)[0], 400, bad)
        self.assertEqual(self.files(self.inbox), [])
        self.assertEqual(self.files(self.outside), [])

    def test_inbox_symlinked_out_of_vault_refused(self):
        self.start()  # load_config refuses it at start, so swap it in afterwards
        os.rmdir(self.inbox)
        os.symlink(self.outside, self.inbox)
        self.assertEqual(self.call("POST", "/note", NOTE)[0], 403)
        self.assertEqual(self.files(self.outside), [])

    def test_inbox_parent_symlinked_out_after_start_refused(self):
        self.start()
        os.makedirs(os.path.join(self.outside, "voice"))
        os.rename(os.path.join(self.vault, "00-Inbox"), os.path.join(self.tmp.name, "moved"))
        os.symlink(self.outside, os.path.join(self.vault, "00-Inbox"))
        self.assertEqual(self.call("POST", "/note", NOTE)[0], 403)
        self.assertEqual(self.files(os.path.join(self.outside, "voice")), [])

    def test_symlink_at_target_name_is_not_followed(self):
        victim = os.path.join(self.outside, "victim.md")
        open(victim, "w").close()
        name = f"{gateway.today()}-codex-heat-pumps-in-old-houses.md"
        os.symlink(victim, os.path.join(self.inbox, name))
        self.assertEqual(self.call("POST", "/note", NOTE)[0], 201)
        self.assertEqual(os.path.getsize(victim), 0)

    def test_oversize_body_is_413(self):
        big = {**NOTE, "body": "x" * (gateway.MAX_BODY + 1)}
        self.assertEqual(self.call("POST", "/note", big)[0], 413)
        self.assertEqual(self.files(self.inbox), [])

    def test_non_post_is_405_and_unknown_route_404(self):
        for method in ("GET", "PUT", "DELETE"):
            self.assertEqual(self.call(method, "/note")[0], 405, method)
        self.assertEqual(self.call("POST", "/nope", NOTE)[0], 404)

    def test_lone_surrogate_is_400_and_leaves_no_file(self):
        raw = b'{"title": "x", "body": "\\ud800", "brain": "codex"}'
        self.assertEqual(self.call("POST", "/note", raw=raw)[0], 400)
        self.assertEqual(self.files(self.inbox), [])

    def test_bad_json_is_400(self):
        self.assertEqual(self.call("POST", "/note", raw=b"{not json")[0], 400)
        self.assertEqual(self.call("POST", "/note", {"title": "x"})[0], 400)


class PeerTests(Base):
    peers = {"192.0.2.5"}

    def test_peer_not_allowed_is_403_even_with_token(self):
        self.assertEqual(self.call("POST", "/note", NOTE)[0], 403)
        self.assertEqual(self.files(self.inbox), [])


class SearchTests(Base):
    def test_unset_search_dirs_searches_only_the_voice_inbox(self):
        self.write("00-Inbox/voice/2026-10-27-codex-heat-pumps.md", "---\ntitle: \"Heat pumps\"\n---\nHeat pumps work.")
        self.write("10-Projects/private.md", "heat pumps secret plan")
        self.write("00-Inbox/other.md", "heat pumps elsewhere")
        status, out = self.call("POST", "/search", {"q": "heat pumps"})
        self.assertEqual(status, 200)
        self.assertEqual([r["path"] for r in out["results"]], ["00-Inbox/voice/2026-10-27-codex-heat-pumps.md"])
        self.assertEqual(out["results"][0]["title"], "Heat pumps")

    def test_path_outside_search_dirs_never_returned(self):
        self.write("10-Projects/a.md", "heat pumps")
        self.write("20-Areas/b.md", "heat pumps")
        self.start(VOICE_GATEWAY_SEARCH_DIRS="10-Projects:00-Inbox/voice")
        paths = [r["path"] for r in self.call("POST", "/search", {"q": "heat"})[1]["results"]]
        self.assertEqual(paths, ["10-Projects/a.md"])

    def test_symlink_out_of_search_dir_not_followed(self):
        with open(os.path.join(self.outside, "leak.md"), "w") as f:
            f.write("heat pumps leaked")
        os.symlink(os.path.join(self.outside, "leak.md"), os.path.join(self.inbox, "leak.md"))
        os.symlink(self.outside, os.path.join(self.inbox, "dir"))
        self.assertEqual(self.call("POST", "/search", {"q": "heat"})[1]["results"], [])

    def test_search_root_swapped_for_symlink_after_start_not_followed(self):
        with open(os.path.join(self.outside, "leak.md"), "w") as f:
            f.write("heat pumps leaked")
        self.start()
        os.rmdir(self.inbox)
        os.symlink(self.outside, self.inbox)
        self.assertEqual(self.call("POST", "/search", {"q": "heat"})[1]["results"], [])

    def test_search_root_ancestor_swapped_for_symlink_after_start_not_followed(self):
        os.makedirs(os.path.join(self.outside, "voice"))
        with open(os.path.join(self.outside, "voice", "leak.md"), "w") as f:
            f.write("heat pumps leaked")
        self.start()
        os.rename(os.path.join(self.vault, "00-Inbox"), os.path.join(self.tmp.name, "moved"))
        os.symlink(self.outside, os.path.join(self.vault, "00-Inbox"))
        self.assertEqual(self.call("POST", "/search", {"q": "heat"})[1]["results"], [])

    def test_fifo_named_md_does_not_hang_search(self):
        os.mkfifo(os.path.join(self.inbox, "pipe.md"))
        self.write("00-Inbox/voice/a.md", "heat pumps")
        paths = [r["path"] for r in self.call("POST", "/search", {"q": "heat"})[1]["results"]]
        self.assertEqual(paths, ["00-Inbox/voice/a.md"])

    def test_search_keeps_the_best_five(self):
        for i in range(8):
            self.write(f"00-Inbox/voice/n{i}.md", "heat " * (i + 1))
        paths = [r["path"] for r in self.call("POST", "/search", {"q": "heat"})[1]["results"]]
        self.assertEqual(paths, [f"00-Inbox/voice/n{i}.md" for i in (7, 6, 5, 4, 3)])

    def test_search_needs_token_and_post(self):
        self.assertEqual(self.call("POST", "/search", {"q": "heat"}, token=None)[0], 401)
        self.assertEqual(self.call("GET", "/search")[0], 405)
        self.assertEqual(self.call("POST", "/search", {"q": ""})[0], 400)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "00-Inbox", "voice"))
        self.env = {"VOICE_GATEWAY_TOKEN": TOKEN, "VOICE_GATEWAY_VAULT": self.tmp.name}

    def refuses(self, **env):
        with self.assertRaises(SystemExit):
            gateway.load_config({**self.env, **env})

    def test_refusals(self):
        self.refuses(VOICE_GATEWAY_TOKEN="")
        self.refuses(VOICE_GATEWAY_TOKEN="short")
        self.refuses(VOICE_GATEWAY_BIND="192.0.2.10")  # LAN bind needs HA_HOST
        for bad in ("../x", "/etc", "00-Inbox/../../x"):
            self.refuses(VOICE_GATEWAY_SEARCH_DIRS=bad)

    def test_lan_bind_allows_ha_host_and_the_station(self):
        cfg = gateway.load_config({**self.env, "VOICE_GATEWAY_BIND": "192.0.2.10", "HA_HOST": "192.0.2.5"})
        self.assertEqual(cfg["peers"], {"192.0.2.5", "192.0.2.10"})
        self.assertEqual(cfg["search_dirs"], [os.path.join("00-Inbox", "voice")])


if __name__ == "__main__":
    unittest.main()
