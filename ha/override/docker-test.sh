#!/usr/bin/env bash
# End-to-end test of install-override.sh against a throwaway HA container:
# install, reinstall (backup), remove, remove again. Never touches a live HA.
# Creates one container and one volume, both named below, and removes both.
# Usage: docker-test.sh --bundle TAR --sha256 HEX --log FILE [--image IMAGE]
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
image=ghcr.io/home-assistant/home-assistant:2026.10.1
name=h7848-override-test
bundle='' want_sha='' logfile=''
while [[ $# -ge 2 ]]; do
	case $1 in
	--bundle) bundle=$2 ;;
	--sha256) want_sha=$2 ;;
	--log) logfile=$2 ;;
	--image) image=$2 ;;
	*) break ;;
	esac
	shift 2
done
if [[ $# -ne 0 || -z $bundle || -z $want_sha || -z $logfile ]]; then
	echo "Usage: docker-test.sh --bundle TAR --sha256 HEX --log FILE [--image IMAGE]" >&2
	exit 2
fi

exec > >(tee "$logfile") 2>&1

if docker container inspect "$name" >/dev/null 2>&1 || docker volume inspect "$name" >/dev/null 2>&1; then
	echo "REFUSED: a container or volume named $name already exists; remove it first" >&2
	exit 2
fi

cleanup() {
	docker rm -f "$name" >/dev/null 2>&1 || true
	docker volume rm "$name" >/dev/null 2>&1 || true
	echo "cleanup: container and volume $name removed"
}
trap cleanup EXIT

step() { echo; echo "== $*"; }
expect() { # name, expected rc, command...
	local what=$1 want=$2 rc=0
	shift 2
	"$@" || rc=$?
	if [[ $rc -ne $want ]]; then
		echo "TEST FAIL: $what: exit $rc, expected $want"
		exit 1
	fi
	echo "TEST ok: $what (exit $rc)"
}
installer() { bash "$here/install-override.sh" "$@" --container "$name"; }

step "start $image"
docker pull "$image" >/dev/null
docker volume create "$name" >/dev/null
docker run -d --name "$name" -v "$name:/config" "$image" >/dev/null
docker image inspect --format 'image digest: {{index .RepoDigests 0}}' "$image"
docker exec "$name" python3 -c 'import homeassistant.const as c; print("HA version:", c.__version__)'
echo "waiting for first start"
for _ in $(seq 60); do
	docker exec "$name" python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8123", timeout=5)' 2>/dev/null && break
	sleep 5
done

step "install"
expect install 0 installer install --bundle "$bundle" --sha256 "$want_sha"
expect "override in place" 0 docker exec "$name" test -f /config/custom_components/anthropic/translations/en.json

step "reinstall backs up the first install"
expect reinstall 0 installer install --bundle "$bundle" --sha256 "$want_sha"
docker exec "$name" ls /config/anthropic-override-backups

step "wrong sha256 is refused"
expect "wrong sha256" 2 installer install --bundle "$bundle" --sha256 0000000000000000000000000000000000000000000000000000000000000000

step "remove"
expect remove 0 installer remove
expect "override gone" 1 docker exec "$name" test -e /config/custom_components/anthropic

step "remove again is a no-op"
expect "remove again" 0 installer remove

echo
echo "ALL PASSED"
