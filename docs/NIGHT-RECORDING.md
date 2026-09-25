# Night Recording — capturing real karaoke nights as test fixtures

Goal: record everything the KJ and singers do during real Thursday nights, so the
mess of the real world (typos, duplicate requests, cancellations, re-orders, retries,
flaky downloads) can be turned into realistic replay fixtures for an end-to-end
simulation suite. The map of *what* can happen lives in
[FUNCTIONALITY-MAP.md](FUNCTIONALITY-MAP.md).

Two complementary recorders:

| Recorder | Where | Captures | Needs restart? |
|---|---|---|---|
| **In-app ActionRecorder** (`kj-controller/action_recorder.py`) | Flask before/after/teardown hooks | Every non-poll HTTP request: actor (kj/singer), method, path, query, **JSON/form body**, **JSON response** (≤64KB, else truncated+sha1), status, duration, client headers (UA, CF-Connecting-IP, Accept-Language, Referer), hashed session cookie | Ships with the app (on by default on the device) |
| **Sidecar** (`kj-controller/scripts/night_capture.py`) | Separate process, stdlib only | Row-level diffs of every table in `rotation.db` + `media_library.db`, `/status` timeline (playback state, current song, volumes, pitch, downloads), `journalctl -u kj-controller -o json`, periodic gz SQLite snapshots | No — read-only against the running app |

Together: the ActionRecorder says **what was asked for**, the sidecar says **what the
system state became** (including background effects: auto-order, gen poller,
downloads, SMS delivery receipts, song-end handling).

## ActionRecorder

- Output: `~/kjdata/action-logs/<night>.jsonl`. `<night>` rolls at **noon**, so a
  show running past midnight stays in one file.
- Config: `action_log_enabled` (default `true` on the device, `false` in
  `create_app(config)` tests), `action_log_dir`.
- Skipped (high-frequency polls / media chunks): `GET /status`, `GET /rotation`,
  `/system/stats`, `/rotation/requests` GET, `/rotation/sync-status`, `/perf/stream`,
  static assets, `/vnc`, preview HLS/CDG/stream segments. Singer 15s polls
  (`/sing/now`, `/sing/rotation`, `/sing/my-requests`, ...) ARE recorded — they show
  what each phone saw and when.
- Never breaks a request: hook errors are swallowed.
- Values are raw (real names, phone numbers). Redaction happens at fixture-build time.

## Sidecar — running it for a night

```bash
scp kj-controller/scripts/night_capture.py nomadpc:/home/nomad/kjdata/night-capture/
ssh nomadpc 'sudo systemd-run --unit=kj-night-capture --uid=nomad --gid=nomad \
  --property=Nice=10 --property=SupplementaryGroups=systemd-journal \
  /usr/bin/python3 /home/nomad/kjdata/night-capture/night_capture.py \
  --out /home/nomad/kjdata/night-captures/$(date +%F)'
# after the show (takes a final snapshot):
ssh nomadpc 'sudo systemctl stop kj-night-capture'
rsync -a nomadpc:/home/nomad/kjdata/night-captures/$N/ $D/capture/
rsync -a nomadpc:/home/nomad/kjdata/action-logs/$N.jsonl $D/actions.jsonl
# Replace the sidecar's journal with the WHOLE night (covers the time before the
# sidecar started; journald keeps ~2 weeks). macOS `date -j` computes next-day noon.
ssh nomadpc "journalctl -u kj-controller -o json --since '$N 16:00' --until '$(date -j -v+1d -f %F $N +%F) 12:00' --no-pager" \
  > $D/capture/journal.jsonl
python3 kj-controller/scripts/night_fixture.py --capture $D/capture --actions $D/actions.jsonl \
  --out $D/fixture [--pseudonymize-names]
```

`night_fixture.py` harvests every phone number it can see (phone-ish DB columns and
body keys), then replaces those digit sequences **in any format, anywhere** (SMS
bodies, Telnyx webhooks, journal lines, JSON columns) with stable fakes
(`+1555000000N`, same person ⇒ same fake). Client IPs → `10.x.y.z`; push
endpoint/keys, Telnyx message ids, edit tokens, session hashes → `redacted-<sha>`.
`redaction.json` holds counts only — the real→fake map is never written.
Tested in `tests/unit/test_night_fixture.py`; also run a leak check against the raw
snapshot before publishing (the kjbox repo is **public** — decide on
`--pseudonymize-names` before committing a fixture).

## Mining a night for edge cases

After a show, scan the ActionRecorder log for every non-2xx response. Each distinct
`(actor, path, status, response.error)` tuple is a candidate regression test or
E2E scenario:

```bash
ssh nomadpc 'python3 - <<EOF
import json, collections
rows = [json.loads(l) for l in open("/home/nomad/kjdata/action-logs/2026-09-24.jsonl")]
bad = [r for r in rows if (r.get("status") or 0) >= 400 and not r["path"].endswith(".php")]
c = collections.Counter((r["actor"], r["method"], r["path"], r["status"],
                         str((r.get("response") or {}).get("error"))[:60]) for r in bad)
for k, v in c.most_common(): print(v, k)
EOF'
```

Then line the timestamps up against `journalctl -k` (SSD drops), `journalctl -u
kj-controller`, and the sidecar's `status.jsonl` to find what was really going on.
The error string the app returned is not always the real cause (see 2026-09-24 below).

Caveats:
- **`actor`** is `singer` (sing blueprint), `kj` (KJ routes), or `anonymous`: an
  unmatched `/sing/...` path. Every public-host request is rewritten under `/sing`,
  so these are almost always internet scanner probes (`/sing/.env`,
  `/sing/wp-login.php`, … about 300 on 2026-09-24). They're kept for security
  visibility. Logs from before v0.115.0 label them `kj`: filter 404s, or classify
  by `host`, before building KJ fixtures from those.
- Singer requests are keyed by `body.device_id`. Before v0.115.0 photo-consent and
  push/subscribe didn't send it. For those older logs, use `client` IP/UA plus
  `session_id` to stitch them to a phone.
- Bodies contain `edit_token`s and full kj_pick `versions[]` snapshots (tens of KB).
  Redact the tokens. Keep the snapshots, because their **size** is the edge case.

## Privacy

Captures contain real singer names and phone numbers. They stay on NomadPC /
Andrew's machine and are **never committed**. Fixtures derived from them must swap
phone numbers for fake ones (`+1555...`) and drop push-subscription endpoints/keys
and Telnyx payload identifiers before landing in the repo.

## Recorded nights

| Night | Sidecar | ActionRecorder | Notes |
|---|---|---|---|
| 2026-09-24 | from 21:11 | from deploy of v0.113.0 (mid-show) | first capture; earlier part of the night is in journald + DB only. Edge cases found (see TESTING.md § "Edge cases from real nights"): kj_pick >50 versions 400 + rate-limit lockout (fixed v0.114.1); photo-consent 429 via venue-IP budget; push/subscribe 400 with no phone; SSD drop at 22:11 misreported as bad ZIP / bad path on `/play`; ~300 scanner 404s labelled `kj` |
