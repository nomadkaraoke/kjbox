# Local catalog mirror — implementation plan (handoff recs 4–6) — 2026-09-22

Spec: workspace `docs/archive/2026-09-19-kjbox-search-unification-handoff.md` § 4, recs 4–6.
Goal: search all three remote catalogs (KN community, KN full, Divebar Drive index) **locally
on the box** with the one shared engine (text_normalize + FTS5 + fuzzy_match), so
rotation/singer search drops to <0.3s, works with dead venue Wi-Fi, and the CF engines are no
longer search-critical (CF stays for download URLs / refresh / stats).

## Verified facts (2026-09-22)

- `gs://nomadkaraoke-divebar-files` is **public** (allUsers objectViewer) and the box's SA
  (`nomad-master-sync@nomadkaraoke.iam.gserviceaccount.com`, key at
  `/opt/nomad/secrets/nomad-master-sync.json`) also has objectViewer. The divebar-mirror CF SA
  has **objectAdmin** on it — can write exports with no IAM change.
- `gs://nomadkaraoke-kn-data` is **private**; contains `full/full-data-latest.json.gz` +
  `community/community-data-latest.json.gz` (gzipped JSON `{"Items":[…]}`), written daily by
  kn-data-sync. Bucket is Pulumi-managed in gen `infrastructure/modules/kn_data_sync.py`.
- `divebar_catalog` BQ table: `gcs_path` lives ONLY in the main table (staging MERGE preserves
  it; sync VM sets it) → the export must SELECT from BigQuery **after** the MERGE, not dump
  the in-memory rows.
- Box has gcloud SDK at `/opt/nomad/google-cloud-sdk/bin`; `scripts/sync_masters.py` is the
  pattern (SA key via `CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE`, lockfile, /rescan poke,
  systemd timer `nomad-master-sync.timer`).
- ExternalCatalog (catalog.py) machinery: FTS5 `unicode61 remove_diacritics 2` over
  normalized text + standalone trigram FTS5 for typo candidates + `fuzzy_match.score`
  full-coverage gate. This IS the shared engine; the mirror reuses the same modules.

## Gen PR A (infrastructure)

1. `functions/divebar_mirror/main.py` + new `export_catalog.py`: Step 4 after
   `load_to_bigquery` — SELECT the CF-search column set (file_id, brand, brand_code, artist,
   title, filename, format, file_size, drive_path, gcs_path→in_gcs) from `divebar_catalog`,
   write gzipped NDJSON to `gs://nomadkaraoke-divebar-files/exports/divebar-catalog-latest.json.gz`
   (custom metadata: row_count, exported_at). Best-effort — export failure never fails the
   index build. Public exposure is unchanged: this data is already publicly queryable via the
   divebar-lookup CF, and the bucket is already public.
2. `modules/kn_data_sync.py`: `BucketIAMMember` — `nomad-master-sync@…` gets
   `roles/storage.objectViewer` on the kn-data bucket (KN data stays private; only the box's
   existing SA can read it).
3. Tests in `functions/divebar_mirror/` for the export helper (stubbed bigquery/storage).
4. Deploy: local `pulumi up`; then force-run `divebar-mirror-refresh`? No — refresh chains
   sync-VM etc. Instead invoke the nightly `divebar-mirror-daily` scheduler job once (or POST
   the CF directly) to produce the first export.

## kjbox PR B (the mirror)

1. **`catalog_mirror.py` — CatalogMirror**: single SQLite DB (default
   `<kjdata>/catalog_mirror.db`, config key `catalog_mirror_db`):
   - `entries(id, source TEXT ('kn_community'|'kn_full'|'divebar'), artist, title,
     payload TEXT(json), norm_text)` — one table, one engine; `payload` carries the
     source-specific fields (watch/brands/file_id/format/…).
   - `entries_fts` FTS5(unicode61 remove_diacritics 2, content=entries) over norm_text;
     `entries_trigram` standalone FTS5(trigram) over norm_text; `mirror_meta` (per-source
     row_count, source_generation/hash, built_at, normalizer_version).
   - Search ladder per source (same as catalog.search): FTS5 MATCH → LIKE fallback →
     trigram candidates + fuzzy_match full-coverage gate. Public API returns REMOTE shapes:
     - `kn_search(query, limit)` → `{"community":[{artist,title,brand,watch}],
       "full":[{artist,title,brands}]}` (drop-in for divebar.kn_search)
     - `divebar_search(query, limit)` → flat CF-style rows (caller groups via
       divebar._group_results, same as today)
   - `is_fresh(max_age_days)` (default 8: survives a week of export-pipeline failure before
     falling back to CF); `built_at`/staleness surfaced in stats.
   - Build: write to `<db>.new` then atomic `os.replace` (NEVER write over a live SQLite);
     `reload()` reopens the connection.
2. **`scripts/sync_catalogs.py`** (pattern: sync_masters.py): lockfile; download
   divebar export via plain HTTPS (public), KN exports via gcloud storage cp with
   `CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE` = `master_sync_credentials_file`; skip rebuild
   when all source hashes match mirror_meta; rebuild + atomic swap; POST
   `/catalog-mirror/reload`. Config keys: `catalog_mirror_enabled` (default False),
   `catalog_mirror_db`, `catalog_mirror_reload_url`. Systemd unit+timer files committed under
   `systemd/` (daily + 15-min-after-boot, Persistent=true), installed manually on the box.
3. **Integration (local-first, CF fallback)**:
   - `app.catalog_mirror = CatalogMirror(cfg)` in app factory.
   - `karaoke_nerds.search(query, cfg, mirror=None)`: fresh mirror → `mirror.kn_search`,
     else `divebar.kn_search` (unchanged path incl. TTL cache).
   - `unified_search`: divebar leg → `mirror.divebar_search` when fresh else
     `divebar.search`. Still submitted to the 2-thread pool (harmless for local).
   - `divebar.find_sibling_audio(..., search_fn=None)`: caller (routes) passes the
     mirror-aware search so approval pairing is also offline-capable.
   - `/karaoke-nerds/search` route passes the mirror (rec 5 groundwork).
   - New `/catalog-mirror/reload` + freshness block in `/system/stats`.
4. **Tests**: CatalogMirror build/search/typo/accent/shape parity vs fixtures; freshness
   gate; atomic swap; sync script hash-skip logic; unified_search local-first + stale-falls-
   back-to-CF (patched); karaoke_nerds.search mirror path.

## kjbox PR C (recs 5+6, after B verified live)

- Rec 5: `/karaoke-nerds/search` server-side composition (in_library, divebar xref, master
  suppression) → panel client code slims down; keep #218/#219 e2e tests green.
- Rec 6: Library local filter typo tolerance — reuse `unified_search(local_only=True)`
  behind a `/search/local` endpoint, or shared test-vector file for the JS filter.

## Rollout / verification

- PR A: pulumi up → trigger index run → verify export object exists + row_count metadata.
- PR B: merge (auto-deploy restarts kj-controller — no live show), scp systemd units +
  `systemctl enable --now nomad-catalog-sync.timer`, run sync once, verify
  `/rotation/search` p50 < 0.5s uncached with mirror fresh, and CF fallback by pointing
  `catalog_mirror_db` at a missing file in a test.
