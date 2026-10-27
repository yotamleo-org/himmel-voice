# gateway/

The voice brains save research into the luna inbox and read it back (HIMMEL-7734). `gateway.py` is a small HTTP service with no LLM in it. It uses the Python standard library only and runs on the station as a systemd user unit. Home Assistant calls it through `ha/packages/voice_gateway.yaml`.

- `POST /note` with `{"title", "body", "brain"}` creates `<vault>/00-Inbox/voice/<date>-<brain>-<slug>.md` with a small frontmatter. It creates nothing else: no directory, no edit, no delete. A name already taken gets a `-2`, `-3`, … suffix. The file opens with `O_EXCL|O_NOFOLLOW`, so a symlink planted at the name is never followed. It refuses a title that looks like a path (`../`, a leading `/` or `\`), a brain outside `[a-z][a-z0-9-]`, and a symlink anywhere on the way to the inbox (`00-Inbox` or `voice`): each step opens by directory fd with `O_NOFOLLOW`, so a folder swapped for a link mid-run cannot redirect the write.
- `POST /search` with `{"q"}` returns up to five `{path, title, snippet}`, ranked by how many query words (three letters or more) a note contains. It is read-only, and it reads only `VOICE_GATEWAY_SEARCH_DIRS`, which defaults to `00-Inbox/voice`. It walks with `os.fwalk` and opens each file with `O_NOFOLLOW`, so no symlink is followed, the search dirs themselves included; dot-directories are skipped.
- Both routes are POST only and need `Authorization: Bearer <VOICE_GATEWAY_TOKEN>`. A request body over 64 KiB is refused.

The token lives in the keyring and reaches the process through `himmel-secrets exec`. The vault path, bind address, port, `HA_HOST` and search dirs live in an untracked file, `~/.config/himmel-voice/gateway.env` (shape: `gateway.env.example`). Only `HA_HOST` and the bind address itself may connect. A LAN bind without `HA_HOST` refuses to start.

**What `/search` returns is sent to cloud LLMs** (Anthropic, OpenAI) by the voice brains. To widen it, add vault subdirectories to `VOICE_GATEWAY_SEARCH_DIRS`, colon-separated and relative to the vault, then restart the unit. Each one must resolve inside the vault, or the gateway refuses to start. Add only folders you would read aloud to a cloud model.

`input-rule.sh` puts a `VOICE-GATEWAY-INPUT` chain at the top of `INPUT`. It lets tcp 8740 through from `HA_HOST` and from the station itself (`lo`), and drops it from everyone else. The rule reads the same untracked `HA_HOST` file as the Wyoming rule. `voice-gateway-input.service` applies it at boot. A firewall flush removes the rule until the next reload; the gateway's own peer check still holds in the meantime. Home Assistant reaches the gateway over plain HTTP, as it reaches Wyoming, so the token and the notes cross the LAN unencrypted.

`python3 -I gateway/test_gateway.py` runs the tests against a temp vault.

## Operator steps

On the station, from the repo root, with `HIMMEL_ROOT` set to a himmel checkout and `<vault>` the luna vault:

    install -d ~/.local/bin ~/.local/lib/himmel-voice ~/.config/himmel-voice ~/.config/systemd/user
    ln -sf "$HIMMEL_ROOT/scripts/lib/himmel-secrets" ~/.local/bin/himmel-secrets
    install -m 644 gateway/gateway.py ~/.local/lib/himmel-voice/
    install -m 600 gateway/gateway.env.example ~/.config/himmel-voice/gateway.env    # then edit it
    openssl rand -hex 32 | ~/.local/bin/himmel-secrets put --stdin VOICE_GATEWAY_TOKEN
    mkdir -p <vault>/00-Inbox/voice
    sudo install -m 755 gateway/input-rule.sh /usr/local/sbin/voice-gateway-input-rule
    sudo install -m 644 gateway/voice-gateway-input.service /etc/systemd/system/
    test -f /etc/himmel-voice/docker-user.env || echo 'HA_HOST=192.0.2.5' | sudo tee /etc/himmel-voice/docker-user.env    # the Home Assistant host
    sudo systemctl daemon-reload
    sudo systemctl enable --now voice-gateway-input.service
    sudo iptables -S VOICE-GATEWAY-INPUT    # lo RETURN, the HA_HOST RETURN, then the DROP
    install -m 644 gateway/voice-gateway.service ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now voice-gateway.service
    journalctl --user -u voice-gateway -n 3    # "voice-gateway on <address>:8740"

The keyring unlocks at login, so the unit retries every 30 s until then. Smoke test from the station (the token reaches curl on stdin, never on a command line):

    ~/.local/bin/himmel-secrets exec --keys VOICE_GATEWAY_TOKEN -- sh -c 'printf "Authorization: Bearer %s\n" "$VOICE_GATEWAY_TOKEN" | curl -sS http://192.0.2.10:8740/search -H @- -H "Content-Type: application/json" -d "{\"q\":\"test\"}"'

To change `HA_HOST`, edit the file and run `sudo systemctl reload voice-gateway-input.service`, then change it in `gateway.env` and run `systemctl --user restart voice-gateway.service`.

In Home Assistant:

1. In `configuration.yaml`, under `homeassistant:`, add `packages: !include_dir_named packages` if it is not there. Then copy `ha/packages/voice_gateway.yaml` to `/config/packages/`.
2. In `secrets.yaml`, add `voice_gateway_note_url` and `voice_gateway_search_url` (`http://<station LAN address>:8740/note` and `/search`), and `voice_gateway_auth: Bearer <token>`. `himmel-secrets get VOICE_GATEWAY_TOKEN` prints the token, so run it only in a terminal of your own.
3. In `research_async`, replace the placeholder agent ids and the satellite id with the real ones (Developer tools, States).
4. Check the configuration, then restart Home Assistant.
5. Expose `script.save_note`, `script.research` and `script.search_luna` to Assist: add them to `ha/allowlist.txt` and run `assist.py apply`. Do not expose `research_async`.

Two traces prove it: "Okay Nabu, research X and save it" (a note appears in `00-Inbox/voice/`, then the box announces it), and "Hey Jarvis, what did Codex find about X?" (the answer comes from that note).
