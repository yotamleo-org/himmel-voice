#!/usr/bin/env bash
# Docker-free tests for install-override.sh: stub `docker` and `ssh` on PATH
# act on a temp dir in place of the container's /config and serve canned logs.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
script=$here/install-override.sh
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT

pass=0
fail=0
check() { # name, then a command that must succeed
	local name=$1
	shift
	if "$@"; then
		pass=$((pass + 1))
	else
		fail=$((fail + 1))
		echo "FAIL: $name" >&2
	fi
}

mkdir -p "$root/bin"
cat >"$root/bin/docker" <<'EOF'
#!/usr/bin/env bash
# Stub docker: exec runs locally against $FAKE_ROOT/config, logs serves $FAKE_ROOT/log.
cmd=$1
shift
case $cmd in
exec)
	[[ $1 == -i ]] && shift
	shift
	[[ $1 == python3 ]] && exit "${FAKE_READY_RC:-0}"
	args=()
	for a in "$@"; do
		[[ $a == /config ]] && a=$FAKE_ROOT/config
		args+=("$a")
	done
	exec "${args[@]}"
	;;
restart) echo "$*" >>"$FAKE_ROOT/restarts"; exit "${FAKE_RESTART_RC:-0}" ;;
logs) cat "$FAKE_ROOT/log" ;;
*) exit 99 ;;
esac
EOF
cat >"$root/bin/ssh" <<'EOF'
#!/usr/bin/env bash
# Stub ssh: `ssh -- HOST CMD` runs CMD through a local shell, like the remote one would.
echo "$2" >>"$FAKE_ROOT/ssh-hosts"
exec sh -c "$3"
EOF
chmod +x "$root/bin/docker" "$root/bin/ssh"
export PATH=$root/bin:$PATH FAKE_ROOT=$root

custom_line='2026-10-27 12:00:00.000 WARNING (SyncWorker_0) [homeassistant.loader] We found a custom integration anthropic which has not been tested by Home Assistant. This component might cause stability problems, be sure to disable it if you experience issues with Home Assistant'
other_line='2026-10-27 12:00:00.000 WARNING (SyncWorker_0) [homeassistant.loader] We found a custom integration tplink which has not been tested by Home Assistant.'

make_bundle() { # out, manifest json, extra entry (optional)
	local out=$1 manifest=$2 extra=${3:-}
	local src
	src=$(mktemp -d "$root/src.XXXX")
	mkdir -p "$src/anthropic"
	printf '%s\n' "$manifest" >"$src/anthropic/manifest.json"
	echo 'NEW = True' >"$src/anthropic/__init__.py"
	if [[ -n $extra ]]; then
		echo x >"$src/evil"
		tar -cf "$out" -C "$src" anthropic "$extra"
	else
		tar -cf "$out" -C "$src" anthropic
	fi
}
sha() { sha256sum "$1" | cut -d' ' -f1; }

reset() { # fresh /config holding another custom integration
	rm -rf "$root/config" "$root/restarts" "$root/ssh-hosts" "$root/log"
	mkdir -p "$root/config/custom_components/tplink"
	echo keep >"$root/config/custom_components/tplink/marker"
	: >"$root/restarts"
}
restarts() { wc -l <"$root/restarts"; }
init_line='2026-10-27 12:00:09.000 INFO (MainThread) [homeassistant.bootstrap] Home Assistant initialized in 9.00s'
log() { printf '%s\n' "$@" "$init_line" >"$root/log"; }

good=$root/good.tar
make_bundle "$good" '{"domain": "anthropic", "version": "2026.10.1-h7771.test"}'
good_sha=$(sha "$good")
install() { bash "$script" install --bundle "$good" --sha256 "$good_sha" --container ha --timeout 5 "$@"; }

reset
log "$custom_line" "$other_line"
out=$(install 2>&1)
check "install: exits 0 on a clean log" test $? -eq 0
check "install: bundle in place" grep -q NEW "$root/config/custom_components/anthropic/__init__.py"
check "install: one restart" test "$(restarts)" -eq 1
check "install: other custom integration untouched" test -f "$root/config/custom_components/tplink/marker"
check "install: reports the custom line" grep -q 'custom integration anthropic' <<<"$out"

reset
mkdir -p "$root/config/custom_components/anthropic"
echo OLD >"$root/config/custom_components/anthropic/__init__.py"
log "$custom_line"
install >/dev/null 2>&1
check "reinstall: exits 0" test $? -eq 0
check "reinstall: old override backed up" grep -rq OLD "$root/config/anthropic-override-backups"
check "reinstall: backup outside custom_components" test ! -e "$root/config/custom_components/anthropic-override-backups"
check "reinstall: new override in place" grep -q NEW "$root/config/custom_components/anthropic/__init__.py"
install >/dev/null 2>&1
check "reinstall twice: each backup kept apart" test "$(find "$root/config/anthropic-override-backups" -mindepth 1 -maxdepth 1 | wc -l)" -eq 2

reset
install --sha256 0000000000000000000000000000000000000000000000000000000000000000 >/dev/null 2>&1
check "sha mismatch: exits 2" test $? -eq 2
check "sha mismatch: nothing installed" test ! -e "$root/config/custom_components/anthropic"
check "sha mismatch: no restart" test "$(restarts)" -eq 0

reset
noversion=$root/noversion.tar
make_bundle "$noversion" '{"domain": "anthropic"}'
bash "$script" install --bundle "$noversion" --sha256 "$(sha "$noversion")" --container ha --timeout 5 >/dev/null 2>&1
check "no version key: exits 2" test $? -eq 2
check "no version key: nothing installed" test ! -e "$root/config/custom_components/anthropic"

reset
stray=$root/stray.tar
make_bundle "$stray" '{"domain": "anthropic", "version": "1"}' evil
bash "$script" install --bundle "$stray" --sha256 "$(sha "$stray")" --container ha --timeout 5 >/dev/null 2>&1
check "entry outside anthropic/: exits 2" test $? -eq 2
check "entry outside anthropic/: nothing installed" test ! -e "$root/config/custom_components/anthropic"

reset
log "$other_line"
install >/dev/null 2>&1
check "install: custom line missing fails" test $? -eq 1

reset
log "$custom_line" '2026-10-27 12:00:01.000 ERROR (MainThread) [homeassistant.bootstrap] Unable to set up core integrations. Activating recovery mode'
install >/dev/null 2>&1
check "install: recovery mode fails" test $? -eq 1

reset
log "$custom_line" '2026-10-27 12:00:02.000 ERROR (MainThread) [custom_components.anthropic] Error fetching data'
install >/dev/null 2>&1
check "install: anthropic error fails" test $? -eq 1

reset
log "$custom_line"
FAKE_READY_RC=1 install >/dev/null 2>&1
check "install: HA never ready fails" test $? -eq 1

reset
printf '%s\n' "$custom_line" >"$root/log"
install >/dev/null 2>&1
check "install: HA never initialized fails" test $? -eq 1

reset
log "$custom_line"
FAKE_RESTART_RC=1 install >/dev/null 2>&1
check "install: failed restart fails" test $? -eq 1

reset
log "$custom_line"
install --ssh ha-host >/dev/null 2>&1
check "ssh install: exits 0" test $? -eq 0
check "ssh install: went through ssh" grep -qx ha-host "$root/ssh-hosts"
check "ssh install: bundle in place" grep -q NEW "$root/config/custom_components/anthropic/__init__.py"

reset
mkdir -p "$root/config/custom_components/anthropic"
echo NEW >"$root/config/custom_components/anthropic/__init__.py"
log "$other_line"
bash "$script" remove --container ha --timeout 5 >/dev/null 2>&1
check "remove: exits 0" test $? -eq 0
check "remove: override gone" test ! -e "$root/config/custom_components/anthropic"
check "remove: override backed up" grep -rq NEW "$root/config/anthropic-override-backups"
check "remove: other custom integration untouched" test -f "$root/config/custom_components/tplink/marker"
check "remove: one restart" test "$(restarts)" -eq 1

reset
mkdir -p "$root/config/custom_components/anthropic"
log "$custom_line"
bash "$script" remove --container ha --timeout 5 >/dev/null 2>&1
check "remove: custom line still there fails" test $? -eq 1

reset
bash "$script" remove --container ha --timeout 5 >/dev/null 2>&1
check "remove: nothing installed exits 0" test $? -eq 0
check "remove: nothing installed, no restart" test "$(restarts)" -eq 0

bash "$script" >/dev/null 2>&1
check "no arguments: exits 2" test $? -eq 2

echo "$pass passed, $fail failed"
test "$fail" -eq 0
