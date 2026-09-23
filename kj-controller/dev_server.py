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


def fetch_real_db():
    """Copy the live rotation.db off nomadpc (read-only on the remote side)."""
    os.makedirs(DEV_DATA_DIR, exist_ok=True)
    print(f"Copying nomadpc:{NOMADPC_DB} -> {REAL_COPY_DB} …")
    subprocess.run(["scp", f"nomadpc:{NOMADPC_DB}", REAL_COPY_DB], check=True)
    print("Done — running against a COPY; the box is untouched.")
    return REAL_COPY_DB


def seed_rotation(app):
    """Seed a realistic mid-night rotation + request queue into empty stores."""
    rotation = app.rotation
    store = app.sing_store

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
                        help="scp the live DB from nomadpc first (read-only copy)")
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
        if args.reseed and os.path.exists(db_path):
            os.unlink(db_path)
            print("Dev DB wiped.")

    from config import load_config
    from app import create_app

    cfg = load_config()
    cfg["rotation_db_path"] = db_path
    # Keep play-stats writes in the dev dir too (never the repo/box copy).
    os.makedirs(DEV_DATA_DIR, exist_ok=True)
    cfg["media_db_path"] = os.path.join(DEV_DATA_DIR, "media_library.db")
    # Never sync a dev rotation to the real Google Sheet.
    cfg.pop("rotation_sheet_id", None)

    app = create_app(config=cfg)

    seed_allowed = not (args.no_seed or args.db or args.fetch_real)
    if seed_allowed and not app.rotation.get_rotation():
        seed_rotation(app)

    token = app.sing_store.ensure_token()
    print(f"\nDB:        {db_path}")
    print("KJ UI:     http://localhost:5555")
    print(f"Singer UI: http://localhost:5555/sing/?t={token}\n")
    app.run(host="127.0.0.1", port=5555, debug=True)


if __name__ == "__main__":
    main()
