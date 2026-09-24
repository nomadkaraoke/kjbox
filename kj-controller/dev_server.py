"""Local dev server running the REAL kjbox backend on a local SQLite DB.

Unlike the old mock-rotation version, this uses the real RotationManager /
SingStore / SmsStore (they all share ``rotation_db_path``), so the singer UI
and KJ UI behave exactly like a live night: /sing/my-requests, wait
estimates, hearts, priority bias, duet pills, tip claims — all real code
paths. VLC/mpv/Chromium managers degrade gracefully on a laptop.

Usage:
  python dev_server.py                 # dev DB at ~/kjdata-dev/rotation.db;
                                       # seeds a realistic mid-night rotation
                                       # if the rotation is empty
  python dev_server.py --reseed        # wipe the dev DB, seed fresh
  python dev_server.py --db PATH       # run against a specific rotation.db
                                       # (e.g. a copy of a real night)
  python dev_server.py --fetch-real    # scp the live DB from nomadpc into
                                       # ~/kjdata-dev/real-night.db (read-only
                                       # copy of the box; never writes back)
                                       # and run against it

Opens at http://localhost:5555 (KJ UI) — the singer UI URL (with the event
token) is printed at startup.
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))

DEV_DATA_DIR = os.path.expanduser("~/kjdata-dev")
DEV_DB = os.path.join(DEV_DATA_DIR, "rotation.db")
REAL_COPY_DB = os.path.join(DEV_DATA_DIR, "real-night.db")
# Where the live box keeps its DB (rotation_db_path default in app.py).
NOMADPC_DB = "kjdata/rotation.db"
# LAN alias first, Cloudflare tunnel fallback for when the Mac isn't at home.
NOMADPC_HOSTS = ("nomadpc", "nomadpctunnel")


def fetch_real_db():
    """Copy the live rotation.db off the box (read-only on the remote side)."""
    os.makedirs(DEV_DATA_DIR, exist_ok=True)
    for host in NOMADPC_HOSTS:
        print(f"Copying {host}:{NOMADPC_DB} -> {REAL_COPY_DB} …")
        proc = subprocess.run(
            ["scp", "-o", "ConnectTimeout=10", f"{host}:{NOMADPC_DB}", REAL_COPY_DB])
        if proc.returncode == 0:
            print("Done — running against a COPY; the box is untouched.")
            return REAL_COPY_DB
    raise SystemExit("Could not reach the box via any of: " + ", ".join(NOMADPC_HOSTS))


def restore_archived_night(db_path, night):
    """Rebuild rotation_entries (in the LOCAL COPY) from an archived night.

    ``night`` is a YYYY-MM-DD night_date or "biggest". Archive rows are all
    terminal (Done), so we restore a believable MID-NIGHT snapshot: the first
    ~40%% stay Done, then one Now Singing, one Up Next, and the rest Waiting.
    Never resets sqlite_sequence (id reuse corrupts cross-night stats).
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if night == "biggest":
            row = conn.execute(
                "SELECT night_date, COUNT(*) n FROM rotation_archive "
                "GROUP BY night_date ORDER BY n DESC LIMIT 1").fetchone()
            if not row:
                raise SystemExit("No archived nights in this DB")
            night = row["night_date"]
        rows = conn.execute(
            "SELECT * FROM rotation_archive WHERE night_date = ? "
            "ORDER BY position, id", (night,)).fetchall()
        if not rows:
            raise SystemExit(f"No archived entries for night {night}")

        done_upto = max(1, int(len(rows) * 0.4))
        conn.execute("DELETE FROM rotation_entries")
        for i, r in enumerate(rows):
            if i < done_upto:
                status = "Done"
            elif i == done_upto:
                status = "Now Singing"
            elif i == done_upto + 1:
                status = "Up Next"
            else:
                status = "Waiting"
            done_at = None
            if status == "Done":
                # Stagger sung times so last-sang ordering looks real.
                done_at = f"2026-01-01 {19 + i // 30}:{(i * 3) % 60:02d}:00"
            conn.execute(
                "INSERT INTO rotation_entries "
                "(singer, song_artist, status, notes, position, file_path, duration, "
                " created_at, updated_at, done_at) "
                "VALUES (?,?,?,?,?,?,?, datetime('now','localtime'), "
                "        datetime('now','localtime'), ?)",
                (r["singer"], r["song_artist"], status, r["notes"] or "",
                 i + 1, r["file_path"], r["duration"], done_at))
        conn.commit()
        print(f"Restored night {night}: {len(rows)} tracks, "
              f"{done_upto} already sung, mid-night snapshot.")
        return night
    finally:
        conn.close()


def link_requests_for_singer(app, name):
    """Attribute a singer's rotation entries to a fresh device via ?r= ids.

    "My songs" is driven by request ids stored on the singer's own phone —
    a review browser has none. Create approved sing_requests linked to every
    entry ``name`` appears in and return the ids for a ?r=1,2,3 URL.
    """
    import json as _json

    store = app.sing_store
    device_id = f"dev-review-{name.lower().replace(' ', '-')}"
    ids = []
    # Read straight from SQLite: get_rotation() drops Done entries, but sung
    # songs are exactly what makes the review realistic. Also clear any
    # attribution rows from a previous run (each restart re-links).
    import sqlite3
    conn = sqlite3.connect(app.rotation.store.db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("DELETE FROM sing_requests WHERE device_id = ?", (device_id,))
        conn.commit()
        entries = conn.execute(
            "SELECT id, singer, song_artist, singers_json FROM rotation_entries").fetchall()
    finally:
        conn.close()
    for e in entries:
        members = None
        if e["singers_json"]:
            try:
                members = _json.loads(e["singers_json"])
            except (ValueError, TypeError):
                members = None
        if name not in (members or [e["singer"]]):
            continue
        # "Artist - Title" is the display convention; best-effort split.
        parts = (e["song_artist"] or "").split(" - ", 1)
        artist, title = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
        req = store.create_request(
            singer_name=name, phone="", song_artist=artist, song_title=title,
            source_type="local", source_ref=e["song_artist"] or "seed",
            source_meta=None, notes="", device_id=device_id,
        )
        store.mark_approved(req["id"], linked_entry_id=e["id"])
        ids.append((req["id"], req.get("edit_token") or ""))
    return ids[:20]   # /sing/my-requests caps at 20 ids per call


def seed_rotation(app):
    """Seed a realistic mid-night rotation + request queue into empty stores."""
    rotation = app.rotation
    store = app.sing_store
    store.set_auto_approve(True)   # Andrew's usual live setting

    def add(singer, song, **kw):
        return rotation.add_entry(singer, song, **kw)["id"]

    # History — two songs already sung tonight (feeds last-sang + fairness).
    for singer, song in (
        ("Sarah B.", "Fleetwood Mac - Dreams"),
        ("Mike", "Journey - Don't Stop Believin'"),
    ):
        eid = add(singer, song)
        rotation.update_status(eid, "Done")

    # Live queue.
    now = add("Lindsay", "Fall Out Boy - Sugar, We're Goin Down")
    rotation.mark_singing(now)

    nxt = add("Andrew", "Maximo Park - Books From Boxes")
    rotation.mark_up_next(nxt)

    add("Greg", "Chappell Roan - Pink Pony Club")
    duet = add("Sarah B.", "Elton John & Kiki Dee - Don't Go Breaking My Heart",
               singers=["Sarah B.", "Mike"])
    tipper = add("Jen", "Adele - Rolling in the Deep")
    rotation.set_paid(tipper, True)                      # ♥ tipped tonight
    rotation.set_singer_priority_bias("Jen", 1)          # … and bumped
    add("Priya", "Whitney Houston - I Wanna Dance with Somebody")
    add("Andrew", "Panic! At the Disco - London Beckoned Songs About Money Written by Machines")
    hold = add("Casey", "Radiohead - Creep", notes="stepped outside")
    rotation.update_status(hold, "On Hold (BRB)")

    # Historical play stats (media_library.db) — feeds the KJ Song Stats
    # panel AND the singer UI's "sung here before?" inspiration section.
    stats = getattr(app, "stats", None)
    if stats is not None and not (stats.overview() or {}).get("plays"):
        history = [
            ("Andrew", "Maxïmo Park", "Books from Boxes", 3),
            ("Andrew", "Foo Fighters", "My Hero", 2),
            ("Andrew", "Billy Joel", "Vienna", 1),
            ("Sarah B.", "Fleetwood Mac", "Dreams", 4),
            ("Jen", "Adele", "Rolling in the Deep", 2),
            ("Mike", "Journey", "Don't Stop Believin'", 5),
        ]
        eid = 1000
        for singer, artist, title, plays in history:
            for n in range(plays):
                eid += 1
                stats.record_play(
                    f"seed-{artist}-{title}".lower().replace(" ", "-"),
                    entry_id=eid, singer=singer, artist=artist, title=title,
                    song_key=f"{artist}|{title}".lower(),
                    played_at=f"2026-0{(n % 6) + 3}-15 21:{10 + n:02d}:00",
                    night_date=f"2026-0{(n % 6) + 3}-15", source="live")

    # Request queue — one pending song + one pending tip claim so the KJ
    # panel has cards to act on.
    store.create_request(
        singer_name="Priya", phone="", song_artist="Carly Rae Jepsen",
        song_title="Call Me Maybe", source_type="local",
        source_ref="/media/CRJ - Call Me Maybe.mp4", source_meta=None,
        notes="", device_id="dev-seed-priya",
    )
    store.create_request(
        singer_name="Greg", phone="", song_artist="", song_title="",
        source_type="tip", source_ref=None,
        source_meta={"amount": 25, "method": "Venmo"},
        notes="Tip claim: $25 via Venmo", device_id="dev-seed-greg",
    )
    print(f"Seeded {len(rotation.get_rotation())} active entries "
          "(+2 sung, 1 pending request, 1 tip claim)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="run against this rotation.db")
    parser.add_argument("--reseed", action="store_true",
                        help="wipe the dev DB and seed fresh")
    parser.add_argument("--fetch-real", action="store_true",
                        help="scp the live DB from the box first (read-only copy; "
                             "tries nomadpc then nomadpctunnel)")
    parser.add_argument("--night", metavar="DATE|biggest",
                        help="restore an archived night (YYYY-MM-DD or 'biggest') "
                             "into the local copy's live rotation as a mid-night "
                             "snapshot — pairs with --fetch-real/--db")
    parser.add_argument("--as", dest="as_singer", default="Andrew",
                        help="singer(s) to attribute in printed 'My songs' "
                             "review URLs — comma-separated (default: Andrew)")
    parser.add_argument("--no-seed", action="store_true",
                        help="skip seeding even if the rotation is empty")
    args = parser.parse_args()

    if args.fetch_real:
        db_path = fetch_real_db()
    elif args.db:
        db_path = os.path.abspath(args.db)
    else:
        os.makedirs(DEV_DATA_DIR, exist_ok=True)
        db_path = DEV_DB
        if args.reseed:
            for f in (db_path, os.path.join(DEV_DATA_DIR, "media_library.db")):
                if os.path.exists(f):
                    os.unlink(f)
            print("Dev DBs wiped (rotation + play stats).")

    if args.night:
        if db_path == DEV_DB:
            raise SystemExit("--night needs a real DB: pair it with "
                             "--fetch-real or --db PATH")
        restore_archived_night(db_path, args.night)

    from config import load_config
    from app import create_app

    cfg = load_config()
    cfg["rotation_db_path"] = db_path
    # Keep play-stats writes in the dev dir too (never the repo/box copy).
    os.makedirs(DEV_DATA_DIR, exist_ok=True)
    cfg["media_db_path"] = os.path.join(DEV_DATA_DIR, "media_library.db")
    # Real song search off the box: the Divebar Cloud Function serves the KN
    # community catalog + GCS-mirror search (unauthenticated HTTP; the local
    # catalog mirror is only the on-box speed layer and gracefully falls
    # through to the CF when absent). Same endpoint the box uses.
    if not cfg.get("divebar_api_url"):
        cfg["divebar_api_url"] = (
            "https://us-central1-nomadkaraoke.cloudfunctions.net/divebar-lookup")
    # Downloads land in the dev dir so song ADDITIONS work end-to-end
    # (divebar/YouTube fetches write here; playback needs the real box).
    media_dir = os.path.join(DEV_DATA_DIR, "videos")
    os.makedirs(media_dir, exist_ok=True)
    cfg["download_folder"] = media_dir
    cfg["media_folders"] = [media_dir]
    # Never sync a dev rotation to the real Google Sheet.
    cfg.pop("rotation_sheet_id", None)

    app = create_app(config=cfg)

    seed_allowed = not (args.no_seed or args.db or args.fetch_real)
    if seed_allowed and not app.rotation.get_rotation():
        seed_rotation(app)

    token = app.sing_store.ensure_token()
    print(f"\nDB:        {db_path}")
    print("KJ UI:     http://localhost:5555")
    print(f"Singer UI: http://localhost:5555/sing/?t={token}")
    # Attribute a singer's entries to a review browser: "My songs" runs off
    # request ids stored on the singer's own phone, so hand the reviewer a
    # ?r= URL carrying freshly-linked ids for their entries.
    for singer in [n.strip() for n in (args.as_singer or "").split(",") if n.strip()]:
        req_ids = link_requests_for_singer(app, singer)
        if req_ids:
            # id:edit_token pairs → the review browser gets full self-service
            # (cancel / change / reorder), exactly like the singer's own phone.
            r = ",".join(f"{i}:{tok}" if tok else str(i) for i, tok in req_ids)
            print(f"Singer UI as {singer} ({len(req_ids)} songs attributed): "
                  f"http://localhost:5555/sing/?t={token}&r={r}")
        else:
            print(f"Singer UI as {singer}: no rotation entries found for that name")
    print()
    # No reloader: it re-executes main() in a child process, which would
    # re-restore --night with fresh entry ids and orphan the ?r= links.
    app.run(host="127.0.0.1", port=5555, debug=True, use_reloader=False)


if __name__ == "__main__":
    main()
