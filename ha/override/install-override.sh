#!/usr/bin/env bash
# Install or remove the anthropic custom_components override in a Home Assistant
# Docker container, on this host or over ssh, then restart HA and check its log.
# Only custom_components/anthropic is touched. See README.md.
set -euo pipefail

usage() {
	cat >&2 <<'EOF'
Usage:
  install-override.sh install --bundle TAR --sha256 HEX --container NAME [--ssh HOST] [--url URL] [--timeout SEC]
  install-override.sh remove --container NAME [--ssh HOST] [--url URL] [--timeout SEC]

  --bundle     tar holding only anthropic/ (with a "version" in anthropic/manifest.json)
  --sha256     expected sha256 of the bundle
  --container  the HA container (config mounted at /config)
  --ssh        run the docker commands on HOST over ssh
  --url        URL HA answers on inside the container (default http://127.0.0.1:8123)
  --timeout    seconds to wait for HA after the restart (default 300)
EOF
	exit 2
}

die() {
	echo "install-override: $*" >&2
	exit 2
}

[[ $# -gt 0 ]] || usage
action=$1
shift
[[ $action == install || $action == remove ]] || usage

bundle='' want_sha='' container='' ssh_host='' url=http://127.0.0.1:8123 timeout=300
while [[ $# -gt 0 ]]; do
	[[ $# -ge 2 ]] || usage
	case $1 in
	--bundle) bundle=$2 ;;
	--sha256) want_sha=$2 ;;
	--container) container=$2 ;;
	--ssh) ssh_host=$2 ;;
	--url) url=$2 ;;
	--timeout) timeout=$2 ;;
	*) usage ;;
	esac
	shift 2
done
[[ -n $container ]] || usage
[[ $action == remove || (-n $bundle && -n $want_sha) ]] || usage

# Runs a command on the target host; stdin passes through, also over ssh.
# Single-quoted for any POSIX remote shell (bash's @Q can emit $'...').
target() {
	if [[ -n $ssh_host ]]; then
		local cmd='' arg
		for arg in "$@"; do
			cmd+=" '${arg//\'/\'\\\'\'}'"
		done
		ssh -- "$ssh_host" "$cmd"
	else
		"$@"
	fi
}

stage=''
trap '[[ -z $stage ]] || rm -rf "$stage"' EXIT

check_bundle() {
	[[ -f $bundle ]] || die "no bundle at $bundle"
	local got
	got=$(sha256sum "$bundle" | cut -d' ' -f1)
	[[ $got == "$want_sha" ]] || die "bundle sha256 is $got, expected $want_sha"
	local entry
	while IFS= read -r entry; do
		[[ $entry == anthropic/* && $entry != *..* ]] || die "bundle entry outside anthropic/: $entry"
	done < <(tar -tf "$bundle")
	stage=$(mktemp -d)
	tar -xf "$bundle" -C "$stage" --no-same-owner
	grep -q '"version"' "$stage/anthropic/manifest.json" 2>/dev/null ||
		die 'bundle has no "version" in anthropic/manifest.json; HA refuses a custom integration without one'
}

# Moves an existing override out of custom_components, so HA cannot load it.
# Exits 3 when there is none.
# shellcheck disable=SC2016 # expanded by the container shell
backup_sh='
set -eu
cc=$1/custom_components
[ -e "$cc/anthropic" ] || exit 3
mkdir -p "$1/anthropic-override-backups"
dest=$1/anthropic-override-backups/anthropic-$2
n=0
while [ -e "$dest" ]; do
	n=$((n + 1))
	dest=$1/anthropic-override-backups/anthropic-$2-$n
done
mv "$cc/anthropic" "$dest"
echo "backup: $dest"
'
# shellcheck disable=SC2016
extract_sh='
set -eu
mkdir -p "$1/custom_components"
tar -x -f - -C "$1/custom_components"
'
ready_py='
import sys, urllib.error, urllib.request
try:
    urllib.request.urlopen(sys.argv[1], timeout=5)
except urllib.error.HTTPError:
    pass
'

backup() {
	local rc=0
	target docker exec "$container" sh -c "$backup_sh" sh /config "$(date -u +%Y%m%dT%H%M%SZ)" || rc=$?
	return "$rc"
}

# Restarts HA and prints its log from the restart on, once HA has answered HTTP
# and logged that it finished starting (HTTP comes up before the config entries do).
restart_and_log() {
	local since waited=0 log
	since=$(target date +%s)
	echo "restarting $container" >&2
	target docker restart "$container" >/dev/null || return 1
	until target docker exec "$container" python3 -c "$ready_py" "$url" 2>/dev/null &&
		log=$(target docker logs --since "$since" "$container" 2>&1) &&
		grep -q "$initialized_re" <<<"$log"; do
		if ((waited >= timeout)); then
			echo "install-override: HA did not finish starting within ${timeout}s; last log lines:" >&2
			target docker logs --since "$since" "$container" 2>&1 | tail -n 20 >&2
			return 1
		fi
		sleep 5
		waited=$((waited + 5))
	done
	printf '%s\n' "$log"
}

initialized_re='Home Assistant initialized'
custom_re='We found a custom integration anthropic '
recovery_re='[Rr]ecovery mode'
error_re='(ERROR|CRITICAL).*anthropic'

verify_installed() {
	local log=$1 ok=0
	if grep -q "$custom_re" <<<"$log"; then
		echo "ok: HA loaded the custom integration anthropic"
	else
		echo "FAIL: no 'custom integration anthropic' line; the override is not loaded" >&2
		ok=1
	fi
	if grep -E "$recovery_re" <<<"$log" >&2; then
		echo "FAIL: HA is in recovery mode, which skips every custom integration" >&2
		ok=1
	fi
	if grep -E "$error_re" <<<"$log" >&2; then
		echo "FAIL: errors mention anthropic (above)" >&2
		ok=1
	fi
	return "$ok"
}

verify_removed() {
	local log=$1 ok=0
	if grep -q "$custom_re" <<<"$log"; then
		echo "FAIL: HA still loads a custom integration anthropic" >&2
		ok=1
	else
		echo "ok: HA runs the built-in anthropic integration"
	fi
	if grep -E "$recovery_re" <<<"$log" >&2; then
		echo "FAIL: HA is in recovery mode" >&2
		ok=1
	fi
	return "$ok"
}

log=''
case $action in
install)
	check_bundle
	backup || [[ $? -eq 3 ]]
	tar -cf - -C "$stage" anthropic | target docker exec -i "$container" sh -c "$extract_sh" sh /config
	echo "installed: /config/custom_components/anthropic"
	log=$(restart_and_log) || exit 1
	verify_installed "$log" || {
		echo "install-override: verification failed; run 'install-override.sh remove' to go back to the built-in integration" >&2
		exit 1
	}
	;;
remove)
	rc=0
	backup || rc=$?
	if ((rc == 3)); then
		echo "nothing to remove: no custom_components/anthropic"
		exit 0
	fi
	((rc == 0)) || exit 1
	log=$(restart_and_log) || exit 1
	verify_removed "$log" || exit 1
	;;
esac
