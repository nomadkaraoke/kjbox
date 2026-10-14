"""``night-clips [YYYY-MM-DD] [--no-open] [--auto-share] [--port N] [--skip-materialize]``"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import webbrowser
from pathlib import Path

from . import clips as clipmod
from . import kjbox, match
from . import select as sel
from .config import HOME, load_config
from .server import ReviewState, make_server


def build_state(night: str, cfg: dict, args, out=print) -> ReviewState:
    work_dir = HOME / night
    work_dir.mkdir(parents=True, exist_ok=True)

    posted = work_dir / "posted.json"
    if posted.exists():
        out(f"!! WARNING: {night} is already marked as POSTED ({posted}). Publishing again will ask to confirm.")

    out(f"Fetching night {night} from kjbox ({cfg['kjbox_host']})...")
    try:
        data = kjbox.fetch_night(night, host=cfg["kjbox_host"])
    except kjbox.KjboxUnreachable as e:
        raise SystemExit(f"ERROR: can't reach the kjbox: {e}\n(no cached consent is used; try again when NomadPC is online)")
    if data["warning"]:
        bar = "!" * 78
        out(f"\n{bar}\n!! {data['warning']}\n{bar}\n")
    songs = match.index_songs(data["songs"])
    if not songs:
        raise SystemExit(f"ERROR: no sung songs recorded for {night}. Wrong date?")

    window = clipmod.night_window(songs)
    camera = Path(cfg["camera_uploads"])
    if not camera.is_dir():
        raise SystemExit(f"ERROR: Camera Uploads folder not found: {camera}")
    clips = clipmod.scan_clips(camera, *window)
    out(f"{len(songs)} songs sung {songs[0]['sung_at'][11:16]}–{songs[-1]['sung_at'][11:16]}; "
        f"{len(clips)} clip(s) in window {window[0]:%m-%d %H:%M} → {window[1]:%m-%d %H:%M}.")

    if clips and not args.skip_materialize:
        out(f"Downloading {len(clips)} clip(s) from Dropbox (dbx-materialize)...")
        failures = clipmod.materialize([c.path for c in clips])
        for path, why in failures.items():
            out(f"  ! {Path(path).name}: {why}")
    out("Probing + thumbnailing...")
    clipmod.prepare(clips, work_dir)
    for c in clips:
        if c.error and c.local:
            out(f"  ! {c.filename}: {c.error}")

    matches = match.match_clips(clips, songs)
    state = ReviewState(
        night, songs, clips, matches, cfg, work_dir,
        consent_source=data["consent_source"], warning=data["warning"], auto_share=args.auto_share,
    )
    if state.load_draft():
        out(f"Loaded saved draft ({state.draft_path}).")
    return state


def summary(state: ReviewState) -> str:
    items = state.items
    n = len(items)
    matched = sum(1 for i in items if i["song_idx"] is not None)
    by = {s: sum(1 for i in items if i["status"] == s) for s in (sel.ELIGIBLE, sel.UNKNOWN, sel.EXCLUDED, sel.UNASSIGNED)}
    flagged = sum(1 for i in items if i["flags"])
    not_local = sum(1 for i in items if not i["local"])
    consenting = sel.consenting_performers(items, state.songs)
    uncovered = sel.uncovered_performers(items, state.songs, state.selected)
    lines = [
        f"Night {state.night} (consent source: {state.consent_source})",
        f"  {n} clips, {matched} matched, {by[sel.UNASSIGNED]} unassigned, {flagged} flagged (check singer)",
        f"  {by[sel.ELIGIBLE]} eligible, {by[sel.UNKNOWN]} consent unknown, {by[sel.EXCLUDED]} excluded (said no)",
        f"  {len(state.selected)}/{state.max_clips} pre-selected; singers covered "
        f"{len(consenting) - len(uncovered)}/{len(consenting)}"
        + (f" (missing: {', '.join(uncovered)})" if uncovered else ""),
    ]
    if not_local:
        lines.append(f"  {not_local} clip(s) not downloaded / unreadable")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="night-clips",
        description="Draft an Instagram carousel from a karaoke night's Camera Uploads clips.",
    )
    ap.add_argument("night", nargs="?", help="show date YYYY-MM-DD (default: last Thursday whose show has ended)")
    ap.add_argument("--no-open", action="store_true", help="don't open the review page in a browser")
    ap.add_argument("--auto-share", action="store_true", help="click Share on Instagram too (default: stop before)")
    ap.add_argument("--port", type=int, default=8765, help="review server port (default 8765, next free if busy)")
    ap.add_argument("--skip-materialize", action="store_true", help="don't download Dropbox placeholders")
    args = ap.parse_args(argv)

    night = args.night or kjbox.default_night()
    try:
        dt.date.fromisoformat(night)
    except ValueError:
        ap.error(f"night must be YYYY-MM-DD, got {night!r}")

    cfg = load_config()
    state = build_state(night, cfg, args)
    print()
    print(summary(state))

    srv = make_server(state, args.port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"\nReview page: {url}  (Ctrl-C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
