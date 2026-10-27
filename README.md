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

Docker's published ports bypass ufw, and the kernel forgets a hand-added `DOCKER-USER` rule on reboot. `docker-user-rule.sh` drops 10200 and 10300 from every address but the Home Assistant host. `wyoming-docker-user.service` applies it before `docker.service` starts, and docker requires it, so docker does not start if the rule cannot be applied. The host address lives in an untracked file outside the repo:

    sudo install -m 755 docker-user-rule.sh /usr/local/sbin/wyoming-docker-user-rule
    sudo install -m 644 wyoming-docker-user.service /etc/systemd/system/
    sudo mkdir -p /etc/himmel-voice
    echo 'HA_HOST=192.0.2.5' | sudo tee /etc/himmel-voice/docker-user.env    # the Home Assistant host's address
    sudo systemctl daemon-reload
    sudo systemctl enable --now wyoming-docker-user.service
    sudo iptables -S DOCKER-USER    # two DROP rules, one per port
    sudo docker compose --env-file images.lock --env-file .env up -d

To change `HA_HOST`, edit the file and run `sudo systemctl reload wyoming-docker-user.service`; the old address's rules are replaced. Do not `restart` the unit: docker requires it and would stop. A `DOCKER-USER` rule added by hand earlier is not touched; delete it once this one is in.

## ha/

`assist.py` configures Home Assistant Assist over the websocket API: entity exposure (`audit`, `list`, `apply <allowlist>`), the voice pipelines (`pipelines`, `debug`, `voice`) and the satellite's wake words (`satellite`). `HA_URL` and `HA_TOKEN` come from the environment. `apply` refuses and unexposes locks, alarm panels and grow-stack or heater entities, and `audit` flags any that are exposed; Home Assistant itself installs no deny rule, so run `audit` after exposing anything by hand. Copy `allowlist.example.txt` to `allowlist.txt` (untracked).
