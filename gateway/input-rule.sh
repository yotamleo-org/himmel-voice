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
# The chain is never flushed, so the fence never stands empty: lo RETURN first, then the
# HA_HOST RETURN, the DROP last, each added only if missing; then every other rule (an earlier
# HA_HOST or port) is deleted, and the three are checked again.
lo=(-i lo -j RETURN)
ha=(-p tcp --dport "$port" -s "$ha_host" -j RETURN)
drop=(-p tcp --dport "$port" -j DROP)
want=("-A $chain -i lo -j RETURN"
      "-A $chain -s $ha_host/32 -p tcp -m tcp --dport $port -j RETURN"
      "-A $chain -p tcp -m tcp --dport $port -j DROP")
if ! iptables -w -C "$chain" "${ha[@]}" 2>/dev/null; then
    pos=1
    iptables -w -C "$chain" "${lo[@]}" 2>/dev/null && pos=2
    iptables -w -I "$chain" "$pos" "${ha[@]}"
fi
iptables -w -C "$chain" "${lo[@]}" 2>/dev/null || iptables -w -I "$chain" 1 "${lo[@]}"
iptables -w -C "$chain" "${drop[@]}" 2>/dev/null || iptables -w -A "$chain" "${drop[@]}"
while IFS= read -r line; do
    case "$line" in -A*) ;; *) continue ;; esac
    keep=0
    for w in "${want[@]}"; do [ "$line" = "$w" ] && keep=1; done
    [ "$keep" = 1 ] && continue
    read -ra del <<<"${line/#-A/-D}"
    iptables -w "${del[@]}"
done < <(iptables -w -S "$chain")
# The delete loop matches iptables -S text; if a build prints a rule another way, it may have
# deleted one of the three. Fail loudly rather than leave the fence open.
for rule in lo ha drop; do
    declare -n r=$rule
    if ! iptables -w -C "$chain" "${r[@]}" 2>/dev/null; then
        echo "input-rule.sh: the $rule rule is missing from $chain after cleanup: the fence is incomplete" >&2
        exit 1
    fi
done
iptables -w -C INPUT -j "$chain" 2>/dev/null || iptables -w -I INPUT 1 -j "$chain"
