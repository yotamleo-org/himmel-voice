#!/usr/bin/env bash
# Rows for docker-user-rule.sh (HIMMEL-7813). The firewall rows run in a private network
# namespace (unshare -rnm); they never touch the host firewall.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
fail=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }

# Row 1: every iptables call waits for the xtables lock.
if grep -vE '^\s*#' "$here/docker-user-rule.sh" | grep -E '\biptables ' | grep -vqE 'iptables -w '; then
    bad "iptables call without -w"
else ok "every iptables call has -w"; fi

# Row 2: a flushed chain is restored by the refresh timer's service running the same script.
[ -f "$here/wyoming-docker-user-refresh.service" ] && [ -f "$here/wyoming-docker-user.timer" ] &&
    grep -q '^ExecStart=/usr/local/sbin/wyoming-docker-user-rule$' "$here/wyoming-docker-user-refresh.service" &&
    grep -q '^OnUnitActiveSec=' "$here/wyoming-docker-user.timer" &&
    ok "refresh timer re-runs the rule script" || bad "no refresh timer re-running the rule script"

# Row 3: after a flush the chain is empty; one re-run (what the timer does) restores both DROPs.
if ! unshare -rnm true 2>/dev/null; then echo "skip rows 3: no unshare -rnm"; exit "$fail"; fi
HA_HOST=192.0.2.5 unshare -rnm bash -c '
    s=$1; drops() { iptables -w -S WYOMING-USER | grep -c -e " -j DROP"; }
    HA_HOST=$HA_HOST bash "$s" || exit 3
    [ "$(drops)" = 2 ] || exit 4
    iptables -w -F WYOMING-USER
    [ "$(drops)" = 0 ] || exit 5
    HA_HOST=$HA_HOST bash "$s" || exit 6
    [ "$(drops)" = 2 ] || exit 7
' _ "$here/docker-user-rule.sh" && ok "flush then re-run restores two DROPs" || bad "flush/re-run row (exit $?)"
exit "$fail"
