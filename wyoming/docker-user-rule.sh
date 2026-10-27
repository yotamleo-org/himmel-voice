#!/usr/bin/env bash
# Limit the Wyoming ports (10200 Piper, 10300 Whisper) to the Home Assistant host (HIMMEL-7778).
# Docker's published ports bypass ufw, so the rule lives in DOCKER-USER; the kernel loses it on
# reboot, so wyoming-docker-user.service re-applies it after docker.service. Idempotent.
# HA_HOST (the Home Assistant host's IPv4 address) comes from the environment, set by the unit's
# untracked EnvironmentFile; it never enters this public repo.
set -euo pipefail

ha_host="${HA_HOST:?set HA_HOST to the Home Assistant host address}"
case "$ha_host" in
    *[!0-9.]*|*..*|.*|*.|'') echo "docker-user-rule.sh: HA_HOST is not an IPv4 address" >&2; exit 2 ;;
esac

for port in 10200 10300; do
    # Docker has already DNATed the packet, so match the port the client dialled.
    rule=(DOCKER-USER -p tcp -m conntrack --ctorigdstport "$port" ! -s "$ha_host" -j DROP)
    iptables -C "${rule[@]}" 2>/dev/null || iptables -I "${rule[@]}"
done
