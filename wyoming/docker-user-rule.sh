#!/usr/bin/env bash
# Limit the Wyoming ports (10200 Piper, 10300 Whisper) to the Home Assistant host (HIMMEL-7778).
# Docker's published ports bypass ufw, so the rule lives in DOCKER-USER; the kernel loses it on
# reboot, so wyoming-docker-user.service applies it before docker.service starts. Idempotent.
# HA_HOST (the Home Assistant host's IPv4 address) comes from the environment, set by the unit's
# untracked EnvironmentFile; it never enters this public repo.
set -euo pipefail

ha_host="${HA_HOST:?set HA_HOST to the Home Assistant host address}"
# Canonical dotted quad only: iptables reads a leading-zero octet as octal.
octet='(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])'
if [[ ! $ha_host =~ ^($octet\.){3}$octet$ ]]; then
    echo "docker-user-rule.sh: HA_HOST is not a canonical IPv4 address" >&2
    exit 2
fi

# Docker keeps a DOCKER-USER chain that already exists, so owning it here puts the rules
# in place before any container can publish a port.
iptables -n -L DOCKER-USER >/dev/null 2>&1 || iptables -N DOCKER-USER

tag=wyoming-docker-user
for port in 10200 10300; do
    # Docker has already DNATed the packet, so match the port the client dialled. --ctdir
    # ORIGINAL keeps the container's replies (source not HA_HOST) out of the match.
    rule=(DOCKER-USER -p tcp -m conntrack --ctorigdstport "$port" --ctdir ORIGINAL
        ! -s "$ha_host" -m comment --comment "$tag" -j DROP)
    iptables -C "${rule[@]}" 2>/dev/null || iptables -I "${rule[@]}"
done

# A changed HA_HOST leaves the old address's rules behind, and two negated rules drop both
# addresses: delete every rule this script tagged that is not for the current HA_HOST.
stale=$(iptables -S DOCKER-USER | grep -F -e "--comment $tag" | grep -F -v -e "! -s $ha_host/32 " || true)
while IFS= read -r line; do
    [ -n "$line" ] || continue
    read -ra del <<<"${line/#-A/-D}"
    iptables "${del[@]}"
done <<<"$stale"
