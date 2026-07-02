# P2 device deploy runbook — download-naming Phase 2 (kjbox)

**Status:** P2 is merged to `main` (kjbox v0.52.0, PR #133) but **NOT on NomadPC** — kjbox autodeploy is
OFF, so `main` merges never reach the device automatically. This is the off-show manual deploy.

**Prereqs:** no live karaoke event running. Also merge/deploy the gen endpoint first (gen PR #864 —
`POST /api/parse-karaoke-titles`; it auto-deploys to Cloud Run on merge). kjbox degrades gracefully if
gen is absent, so ordering is not strict, but the LLM refine only works once gen is live.

## Steps (off-show)

```bash
ssh nomadpctunnel            # NOT nomadpc (that's .local mDNS, LAN-only)

# 1. Back up the DBs (rotation is the important one; media_library + index too)
cp /home/nomad/kjdata/rotation.db /home/nomad/kjdata/rotation.db.bak-$(date +%Y%m%d-%H%M%S)
cp /opt/nomad/kjbox/kj-controller/media_library.db{,.bak-$(date +%Y%m%d-%H%M%S)}
cp /opt/nomad/downloads/media_index.json{,.bak-$(date +%Y%m%d-%H%M%S)} 2>/dev/null || true

# 2. Pull the merged code
cd /opt/nomad/kjbox && git pull

# 3. Restart the service (INTERRUPTS PLAYBACK — off-show only)
sudo systemctl restart kj-controller

# 4. Confirm the version + gen wiring
curl -s http://localhost:5001/system/stats | head      # app on app_bind_port 5001 (NOT 80/Caddy)
# app.js?v=0.52.0 should appear on a hard-refresh of the UI
```

## Verify P2 behavior on-device

1. **New YouTube download** → lands in `/opt/nomad/downloads/youtube/` named
   `Artist - Title [yt-<id>].mp4`, with a `media_library` row (`source=youtube`, media_id `yt-<id>`).
2. **New divebar/community download** → `/opt/nomad/downloads/community/` as
   `Artist - Title [db-<brand>-<fileid>].<ext>` (or `.zip` for cdg pairs).
3. **Dedup-skip** → re-download the same YouTube song; it should link the existing file instead of
   re-downloading (response `deduped: true`; no new queue item).
4. **LLM refine (needs gen live):** confirm downloads get canonical artist/title (esp. a KaraFun-reversed
   title getting fixed). Offline → deterministic name + `needs_review=1` (still works, just unrefined).
5. **Backlog refine (optional):** `cd /opt/nomad/kjbox/kj-controller && venv/bin/python scripts/refine_titles.py`
   (dry-run) → shows how many `needs_review` rows the LLM would upgrade. `--execute` to apply (DB-only,
   does NOT rename files — that's P4).

## Rollback
`cd /opt/nomad/kjbox && git checkout <previous-sha> && sudo systemctl restart kj-controller`, and restore
the DB backups if needed. (P2 only ADDS to media_library / writes new downloads into subfolders; it does
not move or rename existing files, so rollback is low-risk.)
