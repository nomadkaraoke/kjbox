#!/usr/bin/env bash
# Install Caddy as a reverse proxy in front of kj-controller.
#
# Run on the device (NomadPC/NomadPi). Idempotent.
#
# What it does:
#   1. apt install caddy (official repo) if not present.
#   2. Symlink /etc/caddy/Caddyfile → this repo's deploy/Caddyfile.
#   3. Grant the `caddy` user read access to the kj-controller TLS cert + key.
#   4. Flip behind_proxy=true in kj-controller's config.json.
#   5. Restart kj-controller (now binds 127.0.0.1:5001) and enable + restart caddy.

set -euo pipefail

REPO_DIR="/opt/nomad/kjbox"
CERT_DIR="${REPO_DIR}/kj-controller/certs"
CADDYFILE_SRC="${REPO_DIR}/kj-controller/deploy/Caddyfile"
CADDYFILE_DST="/etc/caddy/Caddyfile"
KJ_CONFIG="${REPO_DIR}/kj-controller/config.json"

require_root() {
	if [[ $EUID -ne 0 ]]; then
		echo "error: run with sudo" >&2
		exit 1
	fi
}

install_caddy() {
	if command -v caddy >/dev/null 2>&1; then
		echo "[1/5] caddy already installed ($(caddy version | head -1))"
		return
	fi
	echo "[1/5] installing caddy from official repo..."
	apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
	curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
		| gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
		> /etc/apt/sources.list.d/caddy-stable.list
	apt-get update
	apt-get install -y caddy
}

link_caddyfile() {
	echo "[2/5] linking Caddyfile..."
	mkdir -p /etc/caddy
	if [[ -e "${CADDYFILE_DST}" && ! -L "${CADDYFILE_DST}" ]]; then
		mv "${CADDYFILE_DST}" "${CADDYFILE_DST}.bak.$(date +%s)"
	fi
	ln -sfn "${CADDYFILE_SRC}" "${CADDYFILE_DST}"
	caddy validate --config "${CADDYFILE_DST}" --adapter caddyfile
}

grant_cert_access() {
	echo "[3/5] granting caddy read access to TLS cert + key..."
	# Put both cert and key in a group readable by caddy. Keep files owned by
	# nomad so kj-controller can still use them in legacy (non-proxy) mode.
	chgrp -R caddy "${CERT_DIR}" || true
	chmod 750 "${CERT_DIR}"
	chmod 640 "${CERT_DIR}"/*.pem || true
}

flip_behind_proxy() {
	echo "[4/5] setting behind_proxy=true in ${KJ_CONFIG}..."
	python3 - <<PY
import json, os, tempfile
path = "${KJ_CONFIG}"
cfg = {}
if os.path.exists(path):
    with open(path) as f:
        cfg = json.load(f)
cfg["behind_proxy"] = True
cfg.setdefault("app_bind_host", "127.0.0.1")
cfg.setdefault("app_bind_port", 5001)
d = os.path.dirname(path)
fd, tmp = tempfile.mkstemp(dir=d, suffix=".json")
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
print("  behind_proxy:", cfg["behind_proxy"])
PY
	chown nomad:nomad "${KJ_CONFIG}" || true
}

restart_services() {
	echo "[5/5] restarting kj-controller + caddy..."
	systemctl daemon-reload
	systemctl restart kj-controller
	systemctl enable --now caddy
	systemctl restart caddy
	sleep 2
	systemctl is-active kj-controller
	systemctl is-active caddy
}

require_root
install_caddy
link_caddyfile
grant_cert_access
flip_behind_proxy
restart_services

echo
echo "Done. Verify with:"
echo "  curl -skv https://nomadpc.local/ 2>&1 | grep -E '^(< HTTP|ALPN)'"
