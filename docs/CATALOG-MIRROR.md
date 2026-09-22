# Local catalog mirror — setup runbook

The box mirrors all three remote song catalogs (KaraokeNerds community, KaraokeNerds full,
Divebar Drive index) into ONE local SQLite database (`catalog_mirror.db`) so rotation/singer
searches, the KN panel, and loose-CDG sibling pairing run **on-box** (<50ms, offline-capable)
instead of paying ~1.5s of BigQuery latency per Cloud Function call. Design + rationale:
`docs/archive/2026-09-22-local-catalog-mirror-plan.md`.

## How it works

- `kj-controller/catalog_mirror.py` — the DB + the search engine (same shared stack as the
  external catalog: text_normalize + FTS5 `unicode61 remove_diacritics` + trigram candidates +
  `fuzzy_match` gate). Search methods return the exact remote-API shapes.
- `kj-controller/scripts/sync_catalogs.py` — downloads the three nightly GCS exports,
  hash-skips unchanged sources, rebuilds to `<db>.new` + atomic `os.replace`, POSTs
  `/catalog-mirror/reload`.
- Freshness gate: local serving only while the mirror is < `catalog_mirror_max_age_days`
  (default 8) old; anything else (missing/stale/error/`catalog_mirror_enabled: false`) falls
  back to the Divebar Cloud Function paths unchanged.
- Sources:
  - `https://storage.googleapis.com/nomadkaraoke-divebar-files/exports/divebar-catalog-latest.json.gz`
    (public; produced by the divebar-mirror CF after each nightly index build)
  - `gs://nomadkaraoke-kn-data/{community,full}/…-latest.json.gz` (private; read via the
    master-sync SA key — the SA has `objectViewer` on that bucket, granted in karaoke-gen's
    Pulumi `kn_data_sync` module)

## One-time device setup (NomadPC)

Prereqs already in place from the master sync: SA key at
`/opt/nomad/secrets/nomad-master-sync.json`, Cloud SDK at `/opt/nomad/google-cloud-sdk`.

1. Verify the SA can read the KN bucket (fails until gen's kn-data IAM change is deployed):
   ```bash
   CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE=/opt/nomad/secrets/nomad-master-sync.json \
     /opt/nomad/google-cloud-sdk/bin/gcloud storage ls gs://nomadkaraoke-kn-data/community/ | head
   ```
2. Install the units:
   ```bash
   cd /opt/nomad/kjbox/kj-controller
   sudo cp deploy/nomad-catalog-sync.{service,timer} /etc/systemd/system/
   sudo systemctl daemon-reload
   ```
3. First manual run + verify:
   ```bash
   sudo systemctl start nomad-catalog-sync.service
   journalctl -u nomad-catalog-sync --no-pager | tail -5   # expect counts for 3 sources
   curl -s localhost:5001/system/stats | python3 -c 'import json,sys; print(json.load(sys.stdin)["catalog_mirror"])'
   ```
4. Enable the timer (daily 12:15 UTC + 10min after boot, persistent):
   ```bash
   sudo systemctl enable --now nomad-catalog-sync.timer
   systemctl list-timers nomad-catalog-sync.timer
   ```

## Config keys (`config.json`)

| Key | Default | Meaning |
|---|---|---|
| `catalog_mirror_enabled` | `true` | Kill switch — `false` forces the CF paths everywhere. |
| `catalog_mirror_db` | `""` → `<app dir>/catalog_mirror.db` | DB location. |
| `catalog_mirror_max_age_days` | `8` | Older mirror → CF fallback. |
| `catalog_mirror_reload_url` | `""` → `http://127.0.0.1:<app_bind_port>/catalog-mirror/reload` | Poked after a rebuild. |

## Troubleshooting

- **Searches slow again / `catalog_mirror.usable: false` in `/system/stats`** — check
  `journalctl -u nomad-catalog-sync`; the age/normalizer/kill-switch gate is intentional:
  a broken mirror silently degrades to the pre-mirror CF behavior, never to worse results.
- **KN downloads fail 403** — the SA lost `objectViewer` on `nomadkaraoke-kn-data`
  (managed in karaoke-gen `infrastructure/modules/kn_data_sync.py`).
- **Divebar export missing (404)** — the divebar-mirror CF export step failed; check the
  `catalog_export` field in the CF's nightly sync result (Cloud Logging, `divebar-mirror`).
