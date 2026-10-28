"""Eligibility, pre-selection and caption rendering.

Consent rules (from the spec):

* a clip is **eligible** only if its song is ``postable`` (every performer resolved
  to ``yes``);
* songs where any performer said ``no`` are **excluded** (hidden by default, can't be
  selected);
* anything else is **unknown** (shown greyed; selectable only after an explicit
  confirmation, never pre-selected);
* clips that couldn't be matched to a song are **unassigned** (not selectable).

Pre-selection never picks a flagged (``check_singer``) clip, so an uncertain
attribution can't smuggle a non-consenting singer into the draft.
"""

from __future__ import annotations

import datetime as dt

ELIGIBLE, EXCLUDED, UNKNOWN, UNASSIGNED = "eligible", "excluded", "unknown", "unassigned"


def name_key(name: str) -> str:
    return " ".join((name or "").casefold().split())


def song_status(song: dict | None, custom: bool = False) -> str:
    """``custom`` = the user labelled the clip by hand (e.g. a non-singing moment): their call, so OK."""
    if custom:
        return ELIGIBLE
    if song is None:
        return UNASSIGNED
    perfs = song.get("performers") or []
    if song.get("postable") and perfs and all(p.get("consent") == "yes" for p in perfs):
        return ELIGIBLE
    if any(p.get("consent") == "no" for p in perfs):
        return EXCLUDED
    return UNKNOWN


def is_selectable(item: dict, allow_unknown: bool = False) -> bool:
    if not item.get("local"):
        return False
    if item["status"] == ELIGIBLE:
        return True
    return allow_unknown and item["status"] == UNKNOWN


def _performer_keys(item: dict, songs: list[dict]) -> list[str]:
    if item.get("song_idx") is None:
        return []
    return [name_key(p["name"]) for p in songs[item["song_idx"]]["performers"]]


def consenting_performers(items: list[dict], songs: list[dict]) -> dict[str, str]:
    """``{name_key: display name}`` for performers on eligible songs that have a clip, in show order."""
    seen: dict[str, str] = {}
    for it in sorted(items, key=lambda i: i["start"]):
        if it["status"] != ELIGIBLE or not it.get("local") or it.get("song_idx") is None:
            continue
        for p in songs[it["song_idx"]]["performers"]:
            seen.setdefault(name_key(p["name"]), p["name"])
    return seen


def _spread(cands: list[dict], k: int) -> list[dict]:
    """``k`` items evenly spaced through ``cands`` (already in show order)."""
    if k <= 0:
        return []
    if len(cands) <= k:
        return list(cands)
    if k == 1:
        return [cands[len(cands) // 2]]
    step = (len(cands) - 1) / (k - 1)
    return [cands[round(i * step)] for i in range(k)]


def preselect(items: list[dict], songs: list[dict], max_clips: int = 20) -> list[str]:
    """Clip ids to pre-select, in show order.

    1. One clip per consenting performer: their earliest non-flagged eligible clip.
    2. Fill remaining slots spread across the night, preferring songs not yet shown.
    """
    pool = sorted(
        (i for i in items if is_selectable(i) and not i.get("flags") and i.get("song_idx") is not None),
        key=lambda i: i["start"],
    )
    chosen: list[dict] = []
    covered: set[str] = set()
    for it in pool:
        if len(chosen) >= max_clips:
            break
        keys = _performer_keys(it, songs)
        if any(k not in covered for k in keys):
            chosen.append(it)
            covered.update(keys)

    remaining = max_clips - len(chosen)
    if remaining > 0:
        chosen_ids = {c["id"] for c in chosen}
        chosen_songs = {c["song_idx"] for c in chosen}
        rest = [i for i in pool if i["id"] not in chosen_ids]
        new_songs, seen = [], set(chosen_songs)
        for i in rest:  # earliest clip of each song not represented yet
            if i["song_idx"] not in seen:
                new_songs.append(i)
                seen.add(i["song_idx"])
        picks = _spread(new_songs, remaining)
        chosen.extend(picks)
        remaining -= len(picks)
        if remaining > 0:
            picked = {c["id"] for c in chosen}
            chosen.extend(_spread([i for i in rest if i["id"] not in picked], remaining))

    chosen.sort(key=lambda i: i["start"])
    return [c["id"] for c in chosen[:max_clips]]


def uncovered_performers(items: list[dict], songs: list[dict], selected: list[str]) -> list[str]:
    """Consenting performers (with at least one eligible clip) not in any selected clip."""
    by_id = {i["id"]: i for i in items}
    covered = set()
    for cid in selected:
        if cid in by_id:
            covered.update(_performer_keys(by_id[cid], songs))
    return [name for key, name in consenting_performers(items, songs).items() if key not in covered]


# --- caption ---------------------------------------------------------------

def _date_vars(night: str) -> dict:
    d = dt.date.fromisoformat(night)
    return {"date": night, "date_pretty": f"{d.strftime('%B')} {d.day}", "weekday": d.strftime("%A")}


def performer_credit(p: dict) -> str:
    handle = (p.get("instagram") or "").strip().lstrip("@")
    return f"@{handle}" if handle else (p.get("name") or "").strip()


def render_caption(night: str, selected_items: list[dict], songs: list[dict], caption_cfg: dict) -> str:
    """Header, one line per selected song (in the order given, deduped), footer."""
    vars_ = _date_vars(night)
    header = caption_cfg.get("header", "").format(**vars_).strip()
    lines, seen = [], set()
    for it in selected_items:
        idx = it.get("song_idx")
        if idx is None:
            note = (it.get("note") or "").strip()
            if it.get("custom") and note and note not in seen:
                seen.add(note)
                lines.append(note)
            continue
        if idx in seen:
            continue
        seen.add(idx)
        song = songs[idx]
        perf = " & ".join(performer_credit(p) for p in song["performers"] if performer_credit(p))
        lines.append(caption_cfg.get("line", "{performers} — {song}").format(
            performers=perf or song.get("singer", ""), song=song.get("song", ""), **vars_
        ))
    footer = caption_cfg.get("footer", "").format(**vars_).strip()
    parts = [p for p in (header, "\n".join(lines), footer) if p]
    return "\n\n".join(parts)
