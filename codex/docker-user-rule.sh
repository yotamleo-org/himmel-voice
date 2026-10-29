#!/usr/bin/env bash
# Fence the Codex proxy (HIMMEL-7733). The edge bridge (br-codex-edge, LiteLLM's published port
# 4000) takes new connections only from the Home Assistant host to port 4000 and starts none, so
# LiteLLM has no direct way out. The egress bridge (br-codex-out, squid's) may start only tcp 443
# and DNS; squid.conf narrows 443 to the OpenAI hosts. Docker's published ports bypass ufw, so the
# rules live in DOCKER-USER; the kernel loses them on reboot, so codex-docker-user.service
# applies them before docker.service starts. Idempotent. Nothing here ACCEPTs: traffic that passes
# RETURNs to DOCKER-USER, so the Wyoming chain beside this one still applies.
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

chain=CODEX-USER
edge=br-codex-edge
out=br-codex-out
# Owning DOCKER-USER and its FORWARD jump puts the rules in place before any container can
# publish a port. Some docker versions skip their own jump when the chain already exists
# (moby issue 46147), so the jump is made here too; a second one is harmless.
iptables -w -n -L DOCKER-USER >/dev/null 2>&1 || iptables -w -N DOCKER-USER
iptables -w -C FORWARD -j DOCKER-USER 2>/dev/null || iptables -w -I FORWARD 1 -j DOCKER-USER
iptables -w -n -L "$chain" >/dev/null 2>&1 || iptables -w -N "$chain"

add() { iptables -w -C "$chain" "$@" 2>/dev/null || iptables -w -A "$chain" "$@"; }
# --ctdir ORIGINAL matches the side that started a connection, so replies are never caught.
# Anything else aimed at the edge bridge is dropped: a published port after Docker's DNAT, a
# LAN host routing straight to the container address, another container.
add -o "$edge" -m conntrack --ctdir ORIGINAL -j DROP
# Anything LiteLLM starts itself.
add -i "$edge" -m conntrack --ctdir ORIGINAL -j DROP
add -i "$out" -p tcp -m conntrack --ctorigdstport 443 --ctdir ORIGINAL -j RETURN
add -i "$out" -p udp -m conntrack --ctorigdstport 53 --ctdir ORIGINAL -j RETURN
add -i "$out" -p tcp -m conntrack --ctorigdstport 53 --ctdir ORIGINAL -j RETURN
add -i "$out" -m conntrack --ctdir ORIGINAL -j DROP
# HA_HOST's connection to 4000 must come before the edge DROP, so it is inserted first.
allow=(-s "$ha_host/32" -o "$edge" -p tcp -m conntrack --ctorigdstport 4000 --ctdir ORIGINAL -j RETURN)
iptables -w -C "$chain" "${allow[@]}" 2>/dev/null || iptables -w -I "$chain" 1 "${allow[@]}"
iptables -w -C DOCKER-USER -j "$chain" 2>/dev/null || iptables -w -I DOCKER-USER 1 -j "$chain"

# A connection to the station itself (a bridge gateway or the LAN address) crosses INPUT, not
# FORWARD, so the containers get their own INPUT chain: squid may ask the station's resolver,
# nothing else on the station is reachable from any of the three codex bridges.
input=CODEX-INPUT
iptables -w -n -L "$input" >/dev/null 2>&1 || iptables -w -N "$input"
addin() { iptables -w -C "$input" "$@" 2>/dev/null || iptables -w -A "$input" "$@"; }
addin -i "$out" -p udp -m conntrack --ctorigdstport 53 --ctdir ORIGINAL -j RETURN
addin -i "$out" -p tcp -m conntrack --ctorigdstport 53 --ctdir ORIGINAL -j RETURN
for bridge in "$edge" br-codex-int "$out"; do
    addin -i "$bridge" -m conntrack --ctdir ORIGINAL -j DROP
done
iptables -w -C INPUT -j "$input" 2>/dev/null || iptables -w -I INPUT 1 -j "$input"

# Remove an earlier HA_HOST's rule.
while IFS= read -r line; do
    [ -n "$line" ] || continue
    read -ra del <<<"${line/#-A/-D}"
    iptables -w "${del[@]}"
done < <(iptables -w -S "$chain" | grep -F -e "-o $edge " | grep -F -e ' -s ' | grep -F -v -e "-s $ha_host/32 " || true)
