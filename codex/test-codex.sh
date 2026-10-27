#!/usr/bin/env bash
# Rows for the Codex proxy (HIMMEL-7733): the compose hardening, the one pin site, the squid
# allowlist, and docker-user-rule.sh. The firewall rows run in a private network namespace
# (unshare -rnm); they never touch the host firewall.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
fail=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }

# Row 1: both services carry every hardening key; litellm's only writable mount is the auth dir.
if out=$(python3 -I - "$here/compose.yaml" 2>&1 <<'EOF'
import sys, yaml
svc = yaml.safe_load(open(sys.argv[1]))["services"]
assert set(svc) == {"litellm", "squid"}, sorted(svc)
for name, s in svc.items():
    assert s["image"].startswith("${") and ":?" in s["image"], (name, "image not from images.lock")
    uid = str(s.get("user", "0")).split(":")[0]
    assert uid not in ("", "0", "root"), (name, "runs as root")
    assert s.get("read_only") is True, (name, "read_only")
    assert s.get("cap_drop") == ["ALL"], (name, "cap_drop")
    assert "no-new-privileges:true" in s.get("security_opt", []), (name, "no-new-privileges")
    assert s.get("tmpfs"), (name, "no tmpfs")
    assert "privileged" not in s and "cap_add" not in s and "network_mode" not in s, name
    # the fence is IPv4 only, so no IPv6 (not even link-local) inside the containers
    assert s.get("sysctls", {}).get("net.ipv6.conf.all.disable_ipv6") == 1, (name, "IPv6 on")
for name in svc:
    rw = [v for v in svc[name].get("volumes", []) if not v.endswith(":ro")]
    assert rw == (["./data/auth:/auth"] if name == "litellm" else []), (name, rw)
assert "ports" not in svc["squid"], "squid publishes a port"
assert svc["litellm"]["ports"] == ["${BIND_ADDR:-127.0.0.1}:4000:4000"], svc["litellm"]["ports"]
assert svc["litellm"]["networks"] == ["edge", "internal"], svc["litellm"]["networks"]
assert sorted(svc["squid"]["networks"]) == ["egress", "internal"], svc["squid"]["networks"]
nets = yaml.safe_load(open(sys.argv[1]))["networks"]
assert nets["internal"].get("internal") is True, "internal network is not internal"
bridges = {n: v.get("driver_opts", {}).get("com.docker.network.bridge.name") for n, v in nets.items()}
assert bridges == {"edge": "br-codex-edge", "internal": "br-codex-int", "egress": "br-codex-out"}, bridges
EOF
); then ok "compose: non-root, read-only, no caps, no-new-privileges; only the auth dir is writable"
else bad "compose hardening: $out"; fi

# Row 2: one pin site. Both images are digests in images.lock; compose names no image itself.
lock=$here/images.lock
if [ "$(grep -cE '^(LITELLM|SQUID)_IMAGE=[a-z0-9./_-]+@sha256:[0-9a-f]{64}$' "$lock" 2>/dev/null)" = 2 ] &&
    ! grep -qE '@sha256:|image: [a-z]' "$here/compose.yaml" 2>/dev/null; then
    ok "both digests are pinned in images.lock and nowhere else"
else bad "images.lock is not the one pin site"; fi

# Row 3: one chatgpt/ deployment with an rpm limit, routed so the limit is enforced; the master
# key comes from the environment.
if out=$(python3 -I - "$here/config.yaml" 2>&1 <<'EOF'
import sys, yaml
c = yaml.safe_load(open(sys.argv[1]))
[m] = c["model_list"]
p = m["litellm_params"]
assert p["model"].startswith("chatgpt/"), p["model"]
assert isinstance(p.get("rpm"), int) and p["rpm"] > 0, "no rpm limit"
assert c["router_settings"]["routing_strategy"] == "usage-based-routing-v2", "rpm not enforced"
assert c["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY", "master_key"
EOF
); then ok "config: one chatgpt/ model, rpm enforced, master key from the env"
else bad "config: $out"; fi

# Row 4: squid allows CONNECT :443 to the two hosts and denies everything else.
sq=$here/squid.conf
if [ -f "$sq" ] && [ "$(grep -E '^http_access ' "$sq" | tail -1)" = "http_access deny all" ] &&
    [ "$(grep -cE '^http_access allow ' "$sq")" = 1 ] &&
    grep -qx 'acl codex_hosts dstdomain auth.openai.com chatgpt.com' "$sq" &&
    grep -qx 'http_access deny !CONNECT' "$sq" && grep -qx 'http_access deny !SSL_ports' "$sq" &&
    grep -qx 'http_access allow codex_hosts' "$sq"; then
    ok "squid: CONNECT :443 to auth.openai.com and chatgpt.com only"
else bad "squid allowlist"; fi

# Rows 5-7 run the rule script in a private namespace.
rule=$here/docker-user-rule.sh
if ! unshare -rnm true 2>/dev/null; then echo "FAIL rows 5-7 not run: no unshare -rnm"; exit 1; fi
ns() { unshare -rnm bash -c "export XTABLES_LOCKFILE=\$(mktemp); $1" _ "$rule"; }

# Row 5: ingress to the edge bridge is HA_HOST to port 4000 only; anything the edge bridge
# starts is dropped; the egress bridge may start only tcp 443 and DNS.
want='-A CODEX-USER -s 192.0.2.5/32 -o br-codex-edge -p tcp -m conntrack --ctorigdstport 4000 --ctdir ORIGINAL -j RETURN
-A CODEX-USER -o br-codex-edge -m conntrack --ctdir ORIGINAL -j DROP
-A CODEX-USER -i br-codex-edge -m conntrack --ctdir ORIGINAL -j DROP
-A CODEX-USER -i br-codex-out -p tcp -m conntrack --ctorigdstport 443 --ctdir ORIGINAL -j RETURN
-A CODEX-USER -i br-codex-out -p udp -m conntrack --ctorigdstport 53 --ctdir ORIGINAL -j RETURN
-A CODEX-USER -i br-codex-out -p tcp -m conntrack --ctorigdstport 53 --ctdir ORIGINAL -j RETURN
-A CODEX-USER -i br-codex-out -m conntrack --ctdir ORIGINAL -j DROP
-A CODEX-INPUT -i br-codex-out -p udp -m conntrack --ctorigdstport 53 --ctdir ORIGINAL -j RETURN
-A CODEX-INPUT -i br-codex-out -p tcp -m conntrack --ctorigdstport 53 --ctdir ORIGINAL -j RETURN
-A CODEX-INPUT -i br-codex-edge -m conntrack --ctdir ORIGINAL -j DROP
-A CODEX-INPUT -i br-codex-int -m conntrack --ctdir ORIGINAL -j DROP
-A CODEX-INPUT -i br-codex-out -m conntrack --ctdir ORIGINAL -j DROP'
got=$(ns 'HA_HOST=192.0.2.5 bash "$1" && iptables -w -S CODEX-USER | grep -v "^-N"; iptables -w -S CODEX-INPUT | grep -v "^-N";
    iptables -w -S DOCKER-USER | grep -c -- "-j CODEX-USER"; iptables -w -S INPUT | grep -c -- "-j CODEX-INPUT"' 2>&1)
if [ "$got" = "$want"$'\n1\n1' ]; then ok "only HA_HOST reaches the port; the containers start only 443 and DNS via squid, and nothing on the station but DNS"
else bad "rule set:"$'\n'"$got"; fi

# Row 6: a new HA_HOST replaces the old one's rule; a second run adds no duplicate.
got=$(ns 'HA_HOST=192.0.2.5 bash "$1" && HA_HOST=192.0.2.6 bash "$1" && HA_HOST=192.0.2.6 bash "$1" &&
    iptables -w -S CODEX-USER | grep -c "192.0.2.5"; iptables -w -S CODEX-USER | grep -c "192.0.2.6";
    iptables -w -S | grep -c -- "-j CODEX-USER"; iptables -w -S | grep -c -- "-j CODEX-INPUT"; iptables -w -S CODEX-INPUT | grep -c .' 2>&1)
if [ "$got" = $'0\n1\n1\n1\n6' ]; then ok "a new HA_HOST replaces the old rule; re-runs are idempotent"
else bad "HA_HOST change / idempotence: $got"; fi

# Row 7: a malformed or missing HA_HOST is refused before any rule is written.
got=$(ns 'HA_HOST=192.0.2.05 bash "$1" 2>/dev/null; a=$?; HA_HOST= bash "$1" 2>/dev/null; b=$?;
    echo "$a $b $(iptables -w -S | grep -c CODEX-USER)"' 2>&1)
if [ "$got" = "2 1 0" ]; then ok "a non-canonical or empty HA_HOST is refused"
else bad "HA_HOST refusal: $got"; fi
exit "$fail"
