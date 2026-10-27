#!/usr/bin/env bash
# Limit the voice-gateway port to the Home Assistant host (HIMMEL-7734). The gateway is a host
# process, not a container, so its traffic crosses INPUT; the kernel loses the rule on reboot, so
# voice-gateway-input.service applies it at boot. Idempotent: every run converges the chain,
# so a new HA_HOST replaces the old one. The station's own connections arrive on lo and pass.
# HA_HOST comes from the environment, set by the unit's untracked EnvironmentFile; it never
# enters this public repo. The gateway also refuses any peer but HA_HOST itself.
set -euo pipefail

ha_host="${HA_HOST:?set HA_HOST to the Home Assistant host address}"
port="${VOICE_GATEWAY_PORT:-8740}"
# Canonical dotted quad only: iptables reads a leading-zero octet as octal.
octet='(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])'
if [[ ! $ha_host =~ ^($octet\.){3}$octet$ ]]; then
    echo "input-rule.sh: HA_HOST is not a canonical IPv4 address" >&2
    exit 2
fi
if [[ ! $port =~ ^[1-9][0-9]{0,4}$ ]] || (( port > 65535 )); then
    echo "input-rule.sh: VOICE_GATEWAY_PORT is not a port" >&2
    exit 2
fi

chain=VOICE-GATEWAY-INPUT
iptables -w -n -L "$chain" >/dev/null 2>&1 || iptables -w -N "$chain"
# The chain is never flushed, so the fence never stands empty: the RETURN rules go in at the
# top and the DROP at the bottom, each only if missing, then every other rule (an earlier
# HA_HOST or port) is deleted.
want=("-A $chain -i lo -j RETURN"
      "-A $chain -s $ha_host/32 -p tcp -m tcp --dport $port -j RETURN"
      "-A $chain -p tcp -m tcp --dport $port -j DROP")
iptables -w -C "$chain" -p tcp --dport "$port" -s "$ha_host" -j RETURN 2>/dev/null \
    || iptables -w -I "$chain" 1 -p tcp --dport "$port" -s "$ha_host" -j RETURN
iptables -w -C "$chain" -i lo -j RETURN 2>/dev/null || iptables -w -I "$chain" 1 -i lo -j RETURN
iptables -w -C "$chain" -p tcp --dport "$port" -j DROP 2>/dev/null \
    || iptables -w -A "$chain" -p tcp --dport "$port" -j DROP
while IFS= read -r line; do
    case "$line" in -A*) ;; *) continue ;; esac
    keep=0
    for w in "${want[@]}"; do [ "$line" = "$w" ] && keep=1; done
    [ "$keep" = 1 ] && continue
    read -ra del <<<"${line/#-A/-D}"
    iptables -w "${del[@]}"
done < <(iptables -w -S "$chain")
iptables -w -C INPUT -j "$chain" 2>/dev/null || iptables -w -I INPUT 1 -j "$chain"
