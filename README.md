# himmel-voice
Voice PA for the ESP32-S3-BOX-3 with Home Assistant: Wyoming, ESPHome and HA config as code. A himmel adapter (HIMMEL-7719).

## Hooks

Once per clone, with `HIMMEL_ROOT` pointing at a himmel checkout (the leak-classes gate runs from there):

    pre-commit install -t pre-commit -t commit-msg

They refuse a secret (gitleaks), a LAN address, MAC, home path or station hostname (leak-classes), a commit on `main`, and a commit subject that is not conventional with a `HIMMEL-<n>` ticket.

## wyoming/

Speech-to-text (faster-whisper, `small-int8`, English) and text-to-speech (Piper) for Home Assistant's Wyoming integration. Images are pinned by digest in `images.lock`, the one pin site; containers run non-root with a read-only rootfs.

    cd wyoming
    mkdir -p data/whisper data/piper
    sudo chown -R 1000:1000 data
    echo 'BIND_ADDR=192.0.2.10' > .env    # replace with the station's LAN address
    sudo docker compose --env-file images.lock --env-file .env up -d

The ports bind to 127.0.0.1 unless `BIND_ADDR` is set; `.env` is untracked. The containers run as UID 1000, so `data/` must be theirs.

`python3 -I roundtrip.py [host] ["sentence"] [runs] [stt_port]` speaks a sentence with Piper, transcribes it with Whisper and prints the timings.

Docker's published ports bypass ufw: limit 10200 and 10300 to the Home Assistant host with a `DOCKER-USER` rule.

## ha/

`assist.py` configures Home Assistant Assist over the websocket API: entity exposure (`audit`, `list`, `apply <allowlist>`), the voice pipelines (`pipelines`, `debug`, `voice`) and the satellite's wake words (`satellite`). `HA_URL` and `HA_TOKEN` come from the environment. `apply` refuses and unexposes locks, alarm panels and grow-stack or heater entities, and `audit` flags any that are exposed; Home Assistant itself installs no deny rule, so run `audit` after exposing anything by hand. Copy `allowlist.example.txt` to `allowlist.txt` (untracked).

`aliases <file> [--dry-run]` sets each listed entity's Assist aliases (the spoken names) to exactly the file's list, then re-reads and reports what is still missing; run it with `--dry-run` first. Copy `aliases.example.txt` to `aliases.txt` (untracked). `python3 -I ha/test_assist.py` runs the tests against a stub websocket.

`cap setup <daily cap> <satellite entity> <select entity>=<pipeline name>... [--dry-run]` caps the LLM voice agents per day. It creates a `himmel_cap` counter, a `himmel-cap-local` pipeline (the built-in Home Assistant agent, prefer local intents, the first select's speech engines and voice) and three automations through HA's REST config endpoint, the one the UI editor uses: `himmel_cap_count` counts each state change of the named pipelines' conversation agents, `himmel_cap_switch` selects the local pipeline on every named select past the cap and announces the limit on the satellite, `himmel_cap_reset` resets the counter and restores each select at midnight. The ids are fixed, so a re-run overwrites in place; `cap remove [--dry-run]` deletes the counter, the pipeline and the three automations. The BOX-3 has one pipeline select per wake word, so name both. **Unverified assumption:** one voice request changes each agent entity's state exactly once. Before trusting the cap, speak a few requests and watch the counter in Developer tools.
