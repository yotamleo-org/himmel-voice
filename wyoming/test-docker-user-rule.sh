#!/usr/bin/env bash
# Rows for docker-user-rule.sh (HIMMEL-7813). The firewall rows run in a private network
# namespace (unshare -rnm); they never touch the host firewall.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
fail=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }

# Row 1: every iptables call (each one, even the second on an `a || b` line) waits for the lock.
rule=${RULE_SCRIPT:-$here/docker-user-rule.sh}
if [ "$(grep -vE '^\s*#' "$rule" | grep -oE '\biptables( -w)?' | grep -cx iptables)" != 0 ]; then
    bad "iptables call without -w"
else ok "every iptables call has -w"; fi

# Row 2: a flushed chain is restored by the refresh timer's service running the same script.
[ -f "$here/wyoming-docker-user-refresh.service" ] && [ -f "$here/wyoming-docker-user.timer" ] &&
    grep -q '^ExecStart=/usr/bin/flock /run/wyoming-docker-user.lock /usr/local/sbin/wyoming-docker-user-rule$' "$here/wyoming-docker-user-refresh.service" &&
    grep -q '^OnUnitActiveSec=' "$here/wyoming-docker-user.timer" &&
    grep -q '^ExecReload=/usr/bin/flock /run/wyoming-docker-user.lock ' "$here/wyoming-docker-user.service" &&
    ok "refresh timer re-runs the rule script under flock" || bad "no refresh timer re-running the rule script"

# Row 3: after a flush the chain is empty; one re-run (what the timer does) restores both DROPs.
if ! unshare -rnm true 2>/dev/null; then
    if [ -n "${CI:-}" ]; then bad "row 3 needs unshare -rnm and cannot run here"; else echo "skip row 3: no unshare -rnm"; fi
    exit "$fail"
fi
HA_HOST=192.0.2.5 unshare -rnm bash -c '
    s=$1; lockfile=$(mktemp) || exit 2; trap '"'"'rm -f "$lockfile"'"'"' EXIT; export XTABLES_LOCKFILE=$lockfile
    drops() { iptables -w -S WYOMING-USER | grep -c -e " -j DROP"; }
    HA_HOST=$HA_HOST bash "$s" || exit 3
    [ "$(drops)" = 2 ] || exit 4
    iptables -w -F WYOMING-USER
    [ "$(drops)" = 0 ] || exit 5
    HA_HOST=$HA_HOST bash "$s" || exit 6
    [ "$(drops)" = 2 ] || exit 7
    # two runs without a flush leave no duplicate rule anywhere
    n() { { iptables -w -S WYOMING-USER; iptables -w -S DOCKER-USER; iptables -w -S FORWARD; } | grep -c .; }
    first=$(n); HA_HOST=$HA_HOST bash "$s" || exit 8
    [ "$(n)" = "$first" ] || exit 9
    [ "$(iptables -w -S DOCKER-USER | grep -c -e "-j WYOMING-USER")" = 1 ] || exit 10
    [ "$(iptables -w -S WYOMING-USER | grep -c -e "-j RETURN")" = 2 ] || exit 11
' _ "$here/docker-user-rule.sh" && ok "flush then re-run restores two DROPs; a second run adds no duplicate" || bad "flush/re-run row (exit $?)"
exit "$fail"
