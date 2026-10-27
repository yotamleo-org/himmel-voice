# codex/

"Okay Nabu" on the BOX-3 is answered by Codex, the operator's ChatGPT login, through a LiteLLM proxy and Home Assistant's core `litellm` integration (HIMMEL-7733). Both images are pinned by digest in `images.lock`, the one pin site; a bump is its own PR.

- `litellm` (LiteLLM v1.106.4, `litellm-non_root`) holds the login in `data/auth/auth.json` and serves one model, `chatgpt/gpt-6.1-sol`. It is not a `*-codex` model because Home Assistant calls `/v1/chat/completions` and the `*-codex` models serve `/v1/responses` only. `config.yaml` caps it at 20 requests a minute, and the master key comes from `.env`.
- `squid` is LiteLLM's only way out: CONNECT on 443 to `auth.openai.com` and `chatgpt.com`, nothing else (`squid.conf`). LiteLLM reaches it over an internal network through `HTTPS_PROXY`.
- Both run non-root with a read-only rootfs, all capabilities dropped and no-new-privileges. The auth directory is the only writable mount. The admin UI is off, the cost map is the bundled one, and there is no database, so the master key is the only key.

`docker-user-rule.sh` puts a `CODEX-USER` chain in `DOCKER-USER`. On the edge bridge (`br-codex-edge`, the published port 4000) only the Home Assistant host may open a connection, and only to 4000, and LiteLLM may open none. The squid bridge (`br-codex-out`) may open only tcp 443 and DNS. A connection to the station itself crosses `INPUT`, not `FORWARD`, so a `CODEX-INPUT` chain drops every connection the three codex bridges (edge, `br-codex-int`, out) open to the station, except squid's DNS. The station's own connections are not filtered, so the smoke test below runs on the station. The rule uses the same untracked `HA_HOST` file as the Wyoming rule. `codex-docker-user.service` applies it before `docker.service`, and `codex-docker-user.timer` re-applies it every minute in case a firewall flush wipes it. The rule covers IPv4 only, so both containers disable IPv6 through a sysctl. Do not enable IPv6 on these networks.

`bash test-codex.sh` checks the compose hardening, the pin site, the squid allowlist and the rule set. Its firewall rows run in a private network namespace.

## Operator steps

ChatGPT first: in the account's security settings, allow device-code login for Codex.

On the station, in `codex/`:

    cp .env.example .env    # BIND_ADDR = the station's LAN address; LITELLM_MASTER_KEY = openssl rand -hex 32
    sudo install -d -m 700 -o 65534 -g 65534 data/auth
    sudo install -m 600 -o 65534 -g 65534 /dev/null data/auth/auth.json
    sudo install -m 755 docker-user-rule.sh /usr/local/sbin/codex-docker-user-rule
    sudo install -m 644 codex-docker-user.service codex-docker-user-refresh.service codex-docker-user.timer /etc/systemd/system/
    test -f /etc/himmel-voice/docker-user.env || echo 'HA_HOST=192.0.2.5' | sudo tee /etc/himmel-voice/docker-user.env    # the Home Assistant host
    sudo systemctl daemon-reload
    sudo systemctl enable --now codex-docker-user.service codex-docker-user.timer
    sudo iptables -S CODEX-USER    # the HA_HOST RETURN first, then the DROPs
    sudo iptables -S CODEX-INPUT   # two DNS RETURNs, then a DROP per bridge
    sudo docker compose --env-file images.lock --env-file .env run --rm squid -k parse
    sudo docker compose --env-file images.lock --env-file .env up -d

Log in once (the proxy cannot run the device-code flow itself). The command prints a URL and a code; open the URL, enter the code, then restart LiteLLM so it loads the login:

    sudo docker compose --env-file images.lock --env-file .env exec litellm python -c "from litellm.llms.chatgpt.authenticator import Authenticator; Authenticator().get_access_token()"
    sudo docker compose --env-file images.lock --env-file .env restart litellm

Smoke test from the station (the key is read from `.env` and not printed):

    key=$(sed -n 's/^LITELLM_MASTER_KEY=//p' .env); addr=$(sed -n 's/^BIND_ADDR=//p' .env)
    curl -sS "http://$addr:4000/v1/chat/completions" -H "Authorization: Bearer $key" -H 'Content-Type: application/json' -d '{"model":"chatgpt/gpt-6.1-sol","messages":[{"role":"user","content":"Say ok."}]}'
    sudo docker compose --env-file images.lock --env-file .env logs squid | tail -3    # CONNECT chatgpt.com:443

In Home Assistant:

1. Settings, Devices & services, Add integration, LiteLLM. URL `http://<station LAN address>:4000`, API key = `LITELLM_MASTER_KEY`.
2. On the LiteLLM entry, add a conversation agent named "Nabu-Codex" with model `chatgpt/gpt-6.1-sol`. Leave "Control Home Assistant" off until tool calls are shown to work.
3. Settings, Voice assistants, Add assistant "Nabu-Codex": conversation agent Nabu-Codex with "Prefer handling commands locally" on, speech-to-text Wyoming Whisper, text-to-speech Wyoming Piper. Keep "codex" in the name, because the BOX-3 mascot keys on it.
4. On the BOX-3 device page, set Assistant 2 to Nabu-Codex.

To change `HA_HOST`, edit the file and run `sudo systemctl reload codex-docker-user.service wyoming-docker-user.service`. Do not `restart` either unit: docker requires them and would stop.
