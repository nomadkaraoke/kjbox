#!/usr/bin/env python3
"""Review what singers searched for and what they chose (search_log.py).

Usage (on the NomadPC, or against a copied search_log.db):
  python scripts/search_log_report.py [--db search_log.db] [--days 7] [--all]

Groups events by search session and prints:
  * totals — searches, identification outcomes, choices
  * FLAGGED sessions worth a look (edge cases for docs/SONG-IDENTIFICATION.md):
      - undid a tidy/correction ("keep what I typed")  → identification maybe wrong
      - "not it?" on an identified song                 → wrong identification
      - picked a lower "Which one?" candidate           → ranking to tune
      - edited the pre-filled make-it artist/title      → identification off
      - make-it / YouTube with NO identification         → matcher missed the song
      - Gemini fallback used                             → what the matcher can't do yet
--all prints every session, not just flagged ones.
"""
import argparse
import collections
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_config  # noqa: E402
from search_log import SearchLog, default_db_path  # noqa: E402


def sessions(events):
    by = collections.OrderedDict()
    for e in events:
        by.setdefault(e["search_id"] or f"_nosid_{e['id']}", []).append(e)
    return by


def flags(evs):
    out = []
    idents = [e for e in evs if e["type"] == "identify"]
    last_ident = idents[-1]["data"] if idents else {}
    for e in evs:
        if e["type"] == "resolve":
            out.append("gemini-fallback")
        if e["type"] != "choice":
            continue
        a, d = e["data"].get("action"), e["data"]
        if a == "keep_typed":
            out.append("undid-tidy")
        elif a == "not_it":
            out.append("not-it")
        elif a == "pick_candidate" and d.get("index", 0) > 0:
            out.append(f"picked-candidate-{d.get('index')}")
        elif a == "make_submit" and d.get("edited"):
            out.append("edited-make-prefill")
        elif a in ("make_submit", "youtube_submit") and last_ident.get("status") in (None, "none"):
            out.append(f"{a.split('_')[0]}-without-identification")
    return list(dict.fromkeys(out))


def describe(evs):
    lines = []
    for e in evs:
        t = datetime.fromtimestamp(e["ts"]).strftime("%m-%d %H:%M:%S")
        d = e["data"]
        if e["type"] == "search":
            lines.append(f"  {t} search   {e['query']!r} → {d.get('songs')} karaoke songs {d.get('top', [])[:2]}")
        elif e["type"] == "identify":
            best = d.get("best") or {}
            song = f"{best.get('artist')} — {best.get('title')}" if best else "-"
            lines.append(f"  {t} identify {e['query']!r} → {d.get('status')}/{d.get('kind')} {song} "
                         f"({d.get('ms')} ms) cands={d.get('candidates')}")
        elif e["type"] == "resolve":
            lines.append(f"  {t} gemini   {e['query']!r} → {d.get('kind')} "
                         f"{d.get('canonical_artist')} — {d.get('canonical_title')}")
        else:
            extra = {k: v for k, v in d.items() if k != "action"}
            lines.append(f"  {t} CHOICE   {d.get('action')} {extra}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)
    db = args.db or default_db_path(load_config())
    if not os.path.exists(db):
        print(f"no search log at {db}")
        return 1
    events = SearchLog(db).events(since=time.time() - args.days * 86400)
    by = sessions(events)
    types = collections.Counter(e["type"] for e in events)
    ident = collections.Counter(f'{e["data"].get("status")}/{e["data"].get("kind")}'
                                for e in events if e["type"] == "identify")
    choices = collections.Counter(e["data"].get("action") for e in events if e["type"] == "choice")
    flagged = {sid: flags(evs) for sid, evs in by.items()}
    print(f"Search log {db} — last {args.days:g} days: {len(by)} sessions, events {dict(types)}")
    print(f"identification: {dict(ident.most_common())}")
    print(f"choices:        {dict(choices.most_common())}")
    fc = collections.Counter(f for fl in flagged.values() for f in fl)
    print(f"flags:          {dict(fc.most_common())}\n")
    for sid, evs in by.items():
        if not (args.all or flagged[sid]):
            continue
        print(f"session {sid[:8]}  {', '.join(flagged[sid]) or '-'}")
        print(describe(evs))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
