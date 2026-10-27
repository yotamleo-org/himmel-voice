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

chain=WYOMING-USER
# Owning DOCKER-USER and its FORWARD jump puts the rules in place before any container can
# publish a port. Some docker versions skip their own jump when the chain already exists
# (moby issue 46147), so the jump is made here too; a second one is harmless.
iptables -w -n -L DOCKER-USER >/dev/null 2>&1 || iptables -w -N DOCKER-USER
iptables -w -C FORWARD -j DOCKER-USER 2>/dev/null || iptables -w -I FORWARD 1 -j DOCKER-USER
iptables -w -n -L "$chain" >/dev/null 2>&1 || iptables -w -N "$chain"

# Rules are appended, so they are evaluated in this order. Traffic a container sends arrives
# on a docker bridge: leave it alone (a container's own outbound connection to port 10200 or
# 10300 carries the same original destination port).
for bridge in docker0 br-+; do
    iptables -w -C "$chain" -i "$bridge" -j RETURN 2>/dev/null || iptables -w -A "$chain" -i "$bridge" -j RETURN
done
# Everything else aimed at a Wyoming port, from anyone but HA_HOST, is dropped: a published
# port after Docker's DNAT, and a LAN host routing straight to the container address. --ctdir
# ORIGINAL keeps the container's replies (source not HA_HOST) out of the match.
for port in 10200 10300; do
    rule=(-p tcp -m conntrack --ctorigdstport "$port" --ctdir ORIGINAL ! -s "$ha_host" -j DROP)
    iptables -w -C "$chain" "${rule[@]}" 2>/dev/null || iptables -w -A "$chain" "${rule[@]}"
done
iptables -w -C DOCKER-USER -j "$chain" 2>/dev/null || iptables -w -I DOCKER-USER 1 -j "$chain"

# Remove what an earlier HA_HOST or an earlier version of this script left behind: DROP rules
# for another address (two negated rules drop both addresses), and the rules the first
# version put straight into DOCKER-USER, tagged with a comment.
while IFS= read -r line; do
    [ -n "$line" ] || continue
    read -ra del <<<"${line/#-A/-D}"
    iptables -w "${del[@]}"
done < <(
    { iptables -w -S "$chain" | grep -F -e ' -j DROP' | grep -F -v -e "! -s $ha_host/32 " || true
      iptables -w -S DOCKER-USER | grep -F -e '--comment wyoming-docker-user' || true; }
)
