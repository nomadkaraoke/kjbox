# kj-controller deployment extras

## Why Caddy in front of Flask

Flask's built-in Werkzeug dev server sends `Connection: close` on every
response. Combined with Python's stdlib TLS (~600ms cold handshake), the
browser ends up paying a full TCP+TLS handshake for every asset and every
2-second `/status` poll. On a LAN this makes the UI feel permanently sluggish
even though the server itself is fast.

Caddy in front gives us:

- **HTTP/2 with keep-alive** — all assets + polls share one TLS session.
- **/static/\*** served directly from disk (bypasses Flask entirely).
- **Native HTTP→HTTPS redirect** on :80 (no custom Python listener needed).
- **gzip/zstd compression** of responses.

## Install

```bash
sudo /opt/nomad/kjbox/kj-controller/deploy/install-caddy.sh
```

The script is idempotent. It installs Caddy from the official repo, links
`/etc/caddy/Caddyfile` to this repo's `deploy/Caddyfile`, grants the `caddy`
group read access to the TLS cert+key, sets `behind_proxy: true` in
`config.json`, and restarts both services.

## Rollback

Set `behind_proxy: false` in `config.json` and stop Caddy:

```bash
sudo systemctl stop caddy
sudo systemctl disable caddy
python3 -c "
import json, pathlib
p = pathlib.Path('/opt/nomad/kjbox/kj-controller/config.json')
c = json.loads(p.read_text())
c['behind_proxy'] = False
p.write_text(json.dumps(c, indent=2) + '\n')
"
sudo systemctl restart kj-controller
```

kj-controller will go back to listening on :443 with TLS directly.

## Files

- `Caddyfile` — reverse proxy config. Symlinked to `/etc/caddy/Caddyfile`.
- `install-caddy.sh` — installer/flipper (run with sudo).
