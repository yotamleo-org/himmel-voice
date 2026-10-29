# Anthropic thinking override

Two Anthropic conversation agents on different Claude models that share one
satellite conversation make HA's built-in `anthropic` integration fail with
"Invalid `signature` in `thinking` block" (HIMMEL-7771). Until the fix ships
in a Home Assistant release, the patched integration runs as a
`custom_components/anthropic` override. These scripts install it, remove it,
and check the result. The fix itself is in yotamleo-org/core#2.

**While installed, the override shadows the built-in integration**, also
across HA updates. Remove it once the fix is in the release you run.

## Bundle

A tar holding only `anthropic/`, built from the `homeassistant/components/anthropic/`
of the **same HA release the host runs**, with:

- the HIMMEL-7771 change to `entity.py` applied;
- a `"version"` key in `manifest.json`, which HA requires of a custom integration;
- `translations/en.json` generated from `strings.json`.

A bundle from another release can pin a different `anthropic` library or need
core APIs the host lacks. Build a new one for each HA upgrade.

## Install and remove

The scripts assume HA runs in a Docker container with its config at `/config`,
either on this host or on a host reachable over ssh whose user can run `docker`.
HA OS and HA Core installs are not covered.

```sh
ha/override/install-override.sh install --bundle anthropic-override.tar --sha256 <sha256> --container homeassistant [--ssh <host>]
ha/override/install-override.sh remove --container homeassistant [--ssh <host>]
```

`install`:

1. checks the bundle's sha256, that every entry is under `anthropic/`, and the manifest version;
2. moves any existing override to `/config/anthropic-override-backups/` (outside `custom_components`, so HA does not load it);
3. extracts the bundle into `/config/custom_components/`, then restarts the container;
4. waits until HA answers HTTP and logs "Home Assistant initialized", then reads the log from the restart on.

It passes only when:

- the log has "We found a custom integration anthropic";
- there is no recovery mode, which skips every custom integration;
- no ERROR line mentions anthropic.

`remove` moves the override to the backups dir and restarts HA. It passes when
the custom-integration line is gone and HA is not in recovery mode. With no
override installed it changes nothing.

Only `custom_components/anthropic` is touched; other custom integrations stay.
Exit codes: 0 passed, 1 verification failed, 2 bad arguments or bundle.

## Tests

- `ha/override/test-install-override.sh`: docker-free. It uses stub `docker` and `ssh`
  commands against a temp dir, covering the backup, every refusal, and each log verdict.
- `ha/override/docker-test.sh --bundle TAR --sha256 HEX --log FILE`: runs
  install, reinstall, a wrong sha256, remove and remove-again against a throwaway
  `ghcr.io/home-assistant/home-assistant:2026.10.1` container. It needs docker
  access. It creates one container and one volume, both named `h7848-override-test`,
  and removes both; it refuses to start if either already exists.
