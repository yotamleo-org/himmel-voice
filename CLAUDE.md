# CLAUDE.md

himmel-voice is the voice PA for the ESP32-S3-BOX-3 with Home Assistant: the Wyoming speech services (`wyoming/`) and the Assist config tool (`ha/`). It is a first-party himmel adapter (tracker HIMMEL-7719) and follows himmel's rules. This repo is **public**.

## Never committed

- `ha/allowlist.txt`: the live list of entities exposed to Assist. `ha/allowlist.example.txt` is the tracked shape.
- `wyoming/data/`: model caches and voices downloaded at runtime.
- `.env` and any token. `HA_TOKEN` reaches `assist.py` through the environment only (`himmel-secrets exec`); never read `.env`, never print a token.
- LAN addresses, hostnames and home paths. The leak-classes gate refuses them.

`.gitignore` covers the first three; check with `git check-ignore -v <path>` before staging anything new.

## Ship rules

- Install the hooks once per clone: `pre-commit install -t pre-commit -t commit-msg`, with `HIMMEL_ROOT` set to a himmel checkout.
- Every commit subject is conventional and carries a ticket: `<type>(<scope>): [HIMMEL-<n>] <subject>`.
- No direct commit or push to `main`. Every change goes through a PR carrying the ticket ID, CI green, a judge and a signed GO through the chief; squash merge only.
- CI (`.github/workflows/ci.yml`): gitleaks over the full history, leak-classes over the tree, ruff, a compile and usage smoke.
