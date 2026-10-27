# gateway/

The voice brains save research into the luna inbox, read it back (HIMMEL-7734) and reach the chief (HIMMEL-7735). `gateway.py` is a small HTTP service with no LLM in it. It uses the Python standard library only and runs on the station as a systemd user unit. Home Assistant calls it through `ha/packages/voice_gateway.yaml`.

- `POST /note` with `{"title", "body", "brain"}` creates `<vault>/00-Inbox/voice/<date>-<brain>-<slug>.md` with a small frontmatter. It creates nothing else: no directory, no edit, no delete. A name already taken gets a `-2`, `-3`, … suffix. The file opens with `O_EXCL|O_NOFOLLOW`, so a symlink planted at the name is never followed. It refuses a title that looks like a path (`../`, a leading `/` or `\`), a brain outside `[a-z][a-z0-9-]`, and a symlink anywhere on the way to the inbox (`00-Inbox` or `voice`): each step opens by directory fd with `O_NOFOLLOW`, so a folder swapped for a link mid-run cannot redirect the write.
- `POST /search` with `{"q"}` returns up to five `{path, title, snippet}`, ranked by how many query words (three letters or more) a note contains. It is read-only, and it reads only `VOICE_GATEWAY_SEARCH_DIRS`, which defaults to `00-Inbox/voice`. Each search dir is opened component by component by directory fd with `O_NOFOLLOW`, then walked with `os.fwalk`, and each file opens with `O_NOFOLLOW`. No symlink is followed anywhere, not even one swapped in after start. Only regular files are read, and dot-directories are skipped.
- `POST /chief` with `{"satellite", "text", "chief"?}` appends one line, `- HH:MM [voice satellite=<id> UNVERIFIED-SPEAKER] <text>`, to the inbox of an **armed** chief in `<bridge root>/consoles/<chief>.md`. That is the file himmel's `chief-route.ts` appends Telegram lines to, and it uses the same test for armed: the chief's waiter heartbeat (`<chief>.md.wait`) is under 30 minutes old and the inbox is a regular file. With `chief`, that chief; without it, the single armed one. No armed chief, several armed with no name, or a name that is not armed is a `409` and nothing is written. The gateway opens the inbox `O_APPEND` with no `O_CREAT` and `O_NOFOLLOW`, so it never creates, truncates, renames or follows a link to a file; a `chief` that is not a plain session name (a `/`, `\`, `..` or whitespace) is a `400`. The text is folded to one line (line breaks become ` ⏎ `) and capped at 500 characters, so no spoken text can start a line of its own. The reply is `{"chief", "busy"}`; `busy` means the chief is mid-turn and will pick the line up on its next wait.
- All routes are POST only and need `Authorization: Bearer <VOICE_GATEWAY_TOKEN>`. A request body over 64 KiB is refused.

**Authority of `/chief`.** A voice line is an unverified speaker: a guest or the TV can say it, and the gateway cannot tell. The chief-side rule (himmel, HIMMEL-7735) lets a status or read-only ask, or a narrowing (pause, halt), act directly; anything that widens work (a dispatch, a GO, a push, a merge) only produces a Telegram "voice asked X, reply yes", and a voice line never satisfies `merge-on-green.sh`. The gateway enforces none of that. It only delivers the line, tagged so the chief can tell it from a terminal or Telegram message. `script.ask_chief` is limited to the office satellite, but Home Assistant gives a script no caller identity: the agent passes its own satellite id, and the script refuses any other. Treat that as a guard against the wrong room's agent, not as proof of who is speaking; the chief-side confirm is the bound that holds.

The token lives in the keyring and reaches the process through `himmel-secrets exec`. The vault path, bind address, port, `HA_HOST` and search dirs live in an untracked file, `~/.config/himmel-voice/gateway.env` (shape: `gateway.env.example`). Only `HA_HOST` and the bind address itself may connect. A LAN bind without `HA_HOST` refuses to start.

**What `/search` returns is sent to cloud LLMs** (Anthropic, OpenAI) by the voice brains. To widen it, add vault subdirectories to `VOICE_GATEWAY_SEARCH_DIRS`, colon-separated and relative to the vault, then restart the unit. Each one must resolve inside the vault, or the gateway refuses to start. Add only folders you would read aloud to a cloud model.

`input-rule.sh` puts a `VOICE-GATEWAY-INPUT` chain at the top of `INPUT`. It lets tcp 8740 through from `HA_HOST` and from the station itself (`lo`), and drops it from everyone else. The rule reads the same untracked `HA_HOST` file as the Wyoming rule. `voice-gateway-input.service` applies it at boot. A firewall flush removes the rule until the next reload; the gateway's own peer check still holds in the meantime. Home Assistant reaches the gateway over plain HTTP, as it reaches Wyoming, so the token and the notes cross the LAN unencrypted.

`python3 -I gateway/test_gateway.py` runs the tests against a temp vault.

## Operator steps

On the station, from the repo root, with `HIMMEL_ROOT` set to a himmel checkout and `<vault>` the luna vault:

    install -d ~/.local/bin ~/.local/lib/himmel-voice ~/.config/himmel-voice ~/.config/systemd/user
    printf '#!/bin/sh\nexec "%s/scripts/lib/himmel-secrets" "$@"\n' "$HIMMEL_ROOT" > ~/.local/bin/himmel-secrets
    chmod 755 ~/.local/bin/himmel-secrets    # a wrapper, not a symlink: himmel-secrets loads files beside its real path
    install -m 644 gateway/gateway.py ~/.local/lib/himmel-voice/
    install -m 600 gateway/gateway.env.example ~/.config/himmel-voice/gateway.env    # then edit it
    openssl rand -hex 32 | ~/.local/bin/himmel-secrets put --stdin VOICE_GATEWAY_TOKEN
    mkdir -p <vault>/00-Inbox/voice
    sudo install -m 755 gateway/input-rule.sh /usr/local/sbin/voice-gateway-input-rule
    sudo install -m 644 gateway/voice-gateway-input.service /etc/systemd/system/
    sudo install -d -m 755 /etc/himmel-voice
    test -f /etc/himmel-voice/docker-user.env || echo 'HA_HOST=<Home Assistant IPv4 address>' | sudo tee /etc/himmel-voice/docker-user.env    # the real address: a placeholder fails closed
    sudo systemctl daemon-reload
    sudo systemctl enable --now voice-gateway-input.service
    sudo iptables -S VOICE-GATEWAY-INPUT    # lo RETURN, the HA_HOST RETURN, then the DROP
    install -m 644 gateway/voice-gateway.service ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now voice-gateway.service
    journalctl --user -u voice-gateway -n 3    # "voice-gateway on <address>:8740"

The keyring unlocks at login, so the unit retries every 30 s until then. Smoke test from the station (the token reaches curl on stdin, never on a command line):

    ~/.local/bin/himmel-secrets exec --keys VOICE_GATEWAY_TOKEN -- sh -c 'printf "Authorization: Bearer %s\n" "$VOICE_GATEWAY_TOKEN" | curl -sS http://192.0.2.10:8740/search -H @- -H "Content-Type: application/json" -d "{\"q\":\"test\"}"'

The port and `HA_HOST` live in two files: the fence reads `/etc/himmel-voice/docker-user.env` (`VOICE_GATEWAY_PORT` defaults to 8740 there) and the gateway reads `gateway.env`. Change both together. Edit the first and run `sudo systemctl reload voice-gateway-input.service`, then edit `gateway.env` and run `systemctl --user restart voice-gateway.service`.

In Home Assistant:

1. In `configuration.yaml`, under `homeassistant:`, add `packages: !include_dir_named packages` if it is not there. Then copy `ha/packages/voice_gateway.yaml` to `/config/packages/`.
2. In `secrets.yaml`, add `voice_gateway_note_url` and `voice_gateway_search_url` (`http://<station LAN address>:8740/note` and `/search`), and `voice_gateway_auth: Bearer <token>`. `himmel-secrets get VOICE_GATEWAY_TOKEN | wl-copy` puts the token on the clipboard without showing it; paste it in place of `<token>`.
3. In `research_async`, replace the placeholder agent ids and the satellite id with the real ones (Developer tools, States).
4. Check the configuration, then restart Home Assistant.
5. Expose `script.save_note`, `script.research` and `script.search_luna` to Assist: add them to `ha/allowlist.txt` and run `himmel-secrets exec --keys HA_TOKEN -- python3 -I ha/assist.py apply ha/allowlist.txt` (with `HA_URL` set). Do not expose `research_async`.

6. To enable "ask the chief": add `voice_gateway_chief_url` (`http://<station LAN address>:8740/chief`) and `voice_office_satellite` (the office BOX-3's entity id, from Developer tools, States) to `secrets.yaml` before step 4. A missing secret stops Home Assistant loading the package, so `ask_chief` cannot run without its restriction. Expose `script.ask_chief` to the assistant that serves the office satellite only: add it to the allowlist that agent's `assist.py apply` uses, not to the other satellites' agents. If your chiefs keep their bridge state somewhere other than `~/.claude/handover/bridge`, set `VOICE_GATEWAY_BRIDGE_ROOT` in `gateway.env` and restart the unit.

The chief only receives a line if a chief session is armed on the station (a running chief whose inbox waiter is up). With none, `ask_chief` returns `ok: false` and nothing is written. With several armed it also returns `ok: false` unless the agent passes the optional `chief` field, the session name of the one to reach.

Three traces prove it: "Okay Nabu, research X and save it" (a note appears in `00-Inbox/voice/`, then the box announces it), "Hey Jarvis, what did Codex find about X?" (the answer comes from that note), and the chief: "Hey Jarvis, ask the chief what's blocked" in the office appends a `[voice satellite=… UNVERIFIED-SPEAKER]` line to the armed chief's inbox and a status answer comes back on Telegram; "tell the chief to merge X" produces a confirm DM and no action.
