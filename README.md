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

The ports bind to 127.0.0.1 unless `BIND_ADDR` is set; `.env` is untracked. The containers run as UID 1000, so `data/` must be theirs. Install the firewall rule below, then start the containers, so a LAN-bound port is never open unguarded.

`python3 -I roundtrip.py [host] ["sentence"] [runs] [stt_port]` speaks a sentence with Piper, transcribes it with Whisper and prints the timings.

### GPU speech-to-text (HIMMEL-7834)

The default `whisper` service runs on the CPU. The `gpu` profile adds `whisper-gpu` (linuxserver/faster-whisper `gpu`, `large-v3-turbo` float16 by default, `WHISPER_GPU_MODEL` to change it) on the same port 10300. It needs an NVIDIA driver and nvidia-container-toolkit on the host, and an amd64 card from Turing on. It starts as root under s6 and, read-only, runs the app as UID 911 (linuxserver ignores PUID/PGID then), so it has no `user:` and no `no-new-privileges`; see the PR's Security delta. A plain `docker compose up -d` is unchanged only before the switch: afterwards it would also start the CPU `whisper` on 10300 and conflict, so on a GPU deployment bring the service up only with the targeted `--profile gpu up -d whisper-gpu` below. Stop the CPU one first, since both bind 10300. From `wyoming/`:

    mkdir -p data/whisper-gpu && sudo chown 911:911 data/whisper-gpu
    sudo docker compose --env-file images.lock --env-file .env stop whisper
    sudo docker compose --env-file images.lock --env-file .env --profile gpu up -d whisper-gpu
    sudo docker compose --env-file images.lock --env-file .env logs whisper-gpu    # model loaded on cuda
    nvidia-smi    # the whisper process is listed under the container

Roll back to the CPU:

    sudo docker compose --env-file images.lock --env-file .env --profile gpu stop whisper-gpu
    sudo docker compose --env-file images.lock --env-file .env up -d whisper

`wyoming/bench-stt.py` times faster-whisper directly. Install it into a virtualenv (`python3 -m venv .venv && .venv/bin/pip install faster-whisper`; the GPU also needs the CUDA 12 cuBLAS and cuDNN 9 libraries on the library path), then run `.venv/bin/python -I bench-stt.py large-v3-turbo cuda float16 clip.wav`.

Docker's published ports bypass ufw, and the kernel forgets a hand-added `DOCKER-USER` rule on reboot. `docker-user-rule.sh` drops 10200 and 10300 from every address but the Home Assistant host, whether it dials the published port or routes straight to the container; traffic from the containers' own docker bridges is left alone. The rules live in a `WYOMING-USER` chain that `DOCKER-USER` jumps to. `wyoming-docker-user.service` applies it before `docker.service` starts, and docker requires it, so docker does not start if the rule cannot be applied. The host address lives in an untracked file outside the repo:

    sudo install -m 755 docker-user-rule.sh /usr/local/sbin/wyoming-docker-user-rule
    sudo install -m 644 wyoming-docker-user.service /etc/systemd/system/
    sudo mkdir -p /etc/himmel-voice
    echo 'HA_HOST=192.0.2.5' | sudo tee /etc/himmel-voice/docker-user.env    # the Home Assistant host's address
    sudo systemctl daemon-reload
    sudo systemctl enable --now wyoming-docker-user.service
    sudo iptables -S WYOMING-USER    # two RETURN rules for the docker bridges, then a DROP per port
    sudo docker compose --env-file images.lock --env-file .env up -d

To change `HA_HOST`, edit the file and run `sudo systemctl reload wyoming-docker-user.service`; the old address's rules are replaced. Do not `restart` the unit: docker requires it and would stop. A `DOCKER-USER` rule added by hand earlier is not touched; delete it once this one is in.

## ha/

`assist.py` configures Home Assistant Assist over the websocket API: entity exposure (`audit`, `list`, `apply <allowlist>`), the voice pipelines (`pipelines`, `debug`, `voice`) and the satellite's wake words (`satellite`). `HA_URL` and `HA_TOKEN` come from the environment. `apply` refuses and unexposes locks, alarm panels and grow-stack or heater entities, and `audit` flags any that are exposed; Home Assistant itself installs no deny rule, so run `audit` after exposing anything by hand. Copy `allowlist.example.txt` to `allowlist.txt` (untracked).

`aliases <file> [--dry-run]` sets each listed entity's Assist aliases (the spoken names) to exactly the file's list, then re-reads and reports what is still missing; run it with `--dry-run` first. Copy `aliases.example.txt` to `aliases.txt` (untracked). `python3 -I ha/test_assist.py` runs the tests against a stub websocket.

`cap setup <daily cap in USD> <satellite entity> <select entity>=<pipeline name>... [--dry-run]` caps the LLM voice agents per day. It creates a `himmel_cap` counter, a `himmel_cap_usd_per_request` input number (0.03 USD, set when created, or again if it is still 0, and editable in HA), a `himmel-voice-cap` dashboard with one card (requests today, estimated spend against the cap, capped yes or no, reset time), a `himmel-cap-local` pipeline (the built-in Home Assistant agent, prefer local intents, the first select's speech engines and voice) and three automations through HA's REST config endpoint, the one the UI editor uses: `himmel_cap_count` counts each state change of the named pipelines' conversation agents, `himmel_cap_switch` selects the local pipeline on every named select once the counter times the per-request figure reaches the cap and announces the limit on the satellite, `himmel_cap_reset` resets the counter and restores each select at midnight. The ids are fixed, so a re-run overwrites in place; `cap remove [--dry-run]` deletes the counter, the input number, the dashboard, the pipeline and the three automations. Spend is an estimate: Home Assistant reads no token or cost data from the Anthropic integration, so the real bill can differ; tune the per-request figure against the Anthropic console. The BOX-3 has one pipeline select per wake word, so name both. Setup refuses an entity that does not exist, and stops on any failed write. **The cap fails open:** if the counter, the input number or an automation is missing or unavailable, or the count is wrong, the agents keep answering uncapped, so it limits an estimate and is not a hard ceiling; keep the Anthropic workspace limit as the backstop. **Unverified assumption:** one voice request changes each agent entity's state exactly once. Before trusting the cap, speak a few requests and watch the counter in Developer tools.
