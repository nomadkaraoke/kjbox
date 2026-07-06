#!/usr/bin/env python3
"""Classify the single "original full-mix input audio" file for each NOMAD track
folder in the Dropbox "Tracks-Organized" collection, from a metadata-only
recursive listing (no downloads required).

This is phase 1 of the "original vocals guide" feature: we need exactly one
original recording (the karaoke-gen *input* audio, with the original singer) per
NOMAD brand code, so it can later be layered under the karaoke video as an
adjustable-volume sing-along guide.

The Dropbox folders accumulated ~2 years of inconsistent naming (hand-made era,
early CLI era, web-platform era), so identification is heuristic. Each folder is
scored and assigned a confidence tier:

    HIGH       clear era marker ((Original)/(flacfetch)/(Youtube ..)/(Local)/
               (uploaded)) or filename == "<artist> - <title>". Turnkey.
    MED        exactly one plausible audio file survives exclusions. Very likely
               correct (typically early-era album rips like "06 Song Title.mp3").
    LOW        several plausible files survive; we pick a best guess but it needs
               a human confirm (or phase-2 sync verification will reject it).
    NO_SOURCE  the track was itself sourced from a pre-made karaoke/instrumental
               (e.g. "... (Karaoke) [ytid].webm") -> the original vocals never
               existed. The feature simply won't offer a guide for these.
    GAP        no usable audio at all / genuinely missing. Needs manual sourcing.

The CLI reads a listing produced by:

    rclone lsf -R --files-only --format "sp" --separator "||" \
        "andrewdropboxfull:/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized/" \
        > tracks_listing.txt

and writes a manifest (CSV + JSON) plus a fetch plan (TSV of src -> dst) for the
auto-fetchable tiers.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUDIO_EXT = {"flac", "wav", "mp3", "m4a", "opus", "aac", "ogg", "wma", "webm"}

# Preference when several equally-marked candidates exist. Lossless first, and
# real audio containers ahead of the webm video container (which merely happens
# to carry the audio).
FMT_RANK = {
    "flac": 6, "wav": 5, "m4a": 4, "opus": 4, "aac": 4, "wma": 3,
    "mp3": 3, "ogg": 2, "webm": 1,
}

# Substrings (lowercased) marking a file as a DERIVED / output artifact rather
# than the original input mix. Anything matching is excluded from candidacy.
EXCLUDE_SUBSTRINGS = (
    # "karaoke" anywhere marks an already-instrumental/karaoke render, never the
    # original mix (a real song titled "Karaoke" would be a rare false-exclude
    # that phase-2 surfaces as a gap).
    "karaoke",
    "(instrumental", "instrumental)", "instr.", "instr short", "(instr ",
    "(vocals", "vocals)", "(vocal ", "(lead vocal", "(back vocal",
    "(backing", "backing vocal", "+bv", "(bv ", " bv)",
    "(final", "final karaoke", "(title)", "(title ", "with vocals",
    "(filtered", "mdx", "uvr", "roformer", "model_bs_", "mel_band",
    "_(instrumental)", "_(vocals)", "(clean)", "(drums", "(bass",
    "(other", "(guitar", "(piano", "(no vocals", "(a cappella", "acapella",
    "(acappella", "(click", "(guide track", "(stem", "demucs", "htdemucs",
)

# Positive era markers -> (score, label). Higher score = stronger evidence that
# the file is the original input mix. Kept well above FMT_RANK so format is only
# ever a tiebreak, never the deciding factor.
POSITIVE_MARKERS = (
    (("(original)", "(original "), 100, "original"),
    (("flacfetch",), 100, "flacfetch"),
    (("(uploaded)", "(upload)"), 100, "uploaded"),
    (("(youtube", "(yt "), 90, "youtube"),
    (("(local)", "(local "), 90, "local"),
    (("(spotify", "(soundcloud", "(bandcamp", "(deezer", "(apple music", "(qobuf", "(tidal"),
     90, "streaming-src"),
)

_MARKER_SCORE_FLOOR = 80  # a candidate is "strong" if score-before-format >= this
_NAME_MATCH_SCORE = 80
_BRAND_RE = re.compile(r"^(NOMAD-\d+)\b\s*-?\s*(.*)$", re.IGNORECASE)
_TRACKNO_RE = re.compile(r"^\d{1,2}[\s\-_.]+")


@dataclass
class AudioFile:
    size: int
    path: str          # full path relative to the Tracks-Organized root
    name: str          # basename
    ext: str           # lowercase extension without the dot


@dataclass
class ClassifierResult:
    brand_code: str
    artist: str
    title: str
    tier: str
    method: str
    chosen_path: str | None
    chosen_ext: str | None
    n_audio: int
    n_candidates: int
    alt_candidates: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def auto_fetch(self) -> bool:
        """Tiers we download automatically. LOW is included as a best guess;
        phase-2 sync verification will reject a wrong pick."""
        return self.tier in ("HIGH", "MED", "LOW") and bool(self.chosen_path)


# ---------------------------------------------------------------------------
# Core scoring (pure, unit-tested)
# ---------------------------------------------------------------------------

def is_excluded(name_lower: str) -> bool:
    return any(sub in name_lower for sub in EXCLUDE_SUBSTRINGS)


def marker_score(name_lower: str) -> tuple[int, list[str]]:
    score = 0
    labels: list[str] = []
    for needles, sc, label in POSITIVE_MARKERS:
        if any(n in name_lower for n in needles):
            score += sc
            labels.append(label)
    return score, labels


def _norm_quotes(s: str) -> str:
    """Normalise curly quotes/apostrophes to straight so folder- and file-name
    glyph differences don't defeat exact matching."""
    return (s.replace("’", "'").replace("‘", "'")
             .replace("“", '"').replace("”", '"').replace("′", "'"))


def name_matches_title(name_lower: str, title_lower: str) -> bool:
    """True if the file stem equals the folder's "artist - title" (optionally
    after stripping a leading track number like "06 " or "03 - ")."""
    if not title_lower:
        return False
    title_lower = _norm_quotes(title_lower)
    stem = _norm_quotes(name_lower.rsplit(".", 1)[0])
    stem = _TRACKNO_RE.sub("", stem).strip()
    return stem == title_lower or stem.endswith(title_lower)


def _score_candidate(af: AudioFile, title_lower: str) -> tuple[int, list[str]]:
    name_lower = af.name.lower()
    score, labels = marker_score(name_lower)
    if name_matches_title(name_lower, title_lower):
        score += _NAME_MATCH_SCORE
        labels.append("name-match")
    return score, labels


def classify_folder(brand_code: str, top_name: str, audio_files: list[AudioFile]) -> ClassifierResult:
    """Classify one NOMAD folder. `top_name` is the folder name minus the brand
    prefix, i.e. "<artist> - <title>"."""
    title = top_name.strip()
    title_lower = title.lower()
    artist, song = _split_artist_title(title)

    note = ""
    tl = title_lower
    if "(short version)" in tl or "(short karaoke version)" in tl or "(short)" in tl:
        note = "short-version-edit"

    candidates: list[tuple[int, AudioFile, list[str]]] = []
    excluded: list[AudioFile] = []
    for af in audio_files:
        if is_excluded(af.name.lower()):
            excluded.append(af)
            continue
        score, labels = _score_candidate(af, title_lower)
        candidates.append((score, af, labels))

    # sort by (marker/name score desc, format rank desc, size desc)
    candidates.sort(key=lambda t: (t[0], FMT_RANK.get(t[1].ext, 0), t[1].size), reverse=True)

    base = dict(
        brand_code=brand_code, artist=artist, title=song,
        n_audio=len(audio_files), note=note,
    )

    if not candidates:
        # Everything was excluded (or there was no audio at all). Decide whether
        # the source was inherently karaoke (no original possible) or a true gap.
        excluded_join = " | ".join(a.name.lower() for a in excluded)
        if excluded and "karaoke" in excluded_join:
            ns_base = {**base, "note": note or "source-was-karaoke"}
            return ClassifierResult(tier="NO_SOURCE", method="karaoke-sourced",
                                    chosen_path=None, chosen_ext=None,
                                    n_candidates=0, **ns_base)
        return ClassifierResult(tier="GAP", method="none", chosen_path=None,
                                chosen_ext=None, n_candidates=0, **base)

    best_score, best_af, best_labels = candidates[0]
    alts = [c[1].path for c in candidates[1:4]]

    strong = [c for c in candidates if c[0] >= _MARKER_SCORE_FLOOR]
    if strong:
        method = "+".join(best_labels) or "marker"
        tier = "HIGH"
    elif len(candidates) == 1:
        method = "leftover-only"
        tier = "MED"
    else:
        method = "leftover-ambiguous"
        tier = "LOW"

    return ClassifierResult(
        tier=tier, method=method, chosen_path=best_af.path, chosen_ext=best_af.ext,
        n_candidates=len(candidates), alt_candidates=alts, **base,
    )


def _split_artist_title(top_name: str) -> tuple[str, str]:
    """Best-effort split of "<artist> - <title>" -> (artist, title). Falls back
    to ('', whole) when there's no separator."""
    parts = top_name.split(" - ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", top_name.strip()


# ---------------------------------------------------------------------------
# Listing parsing + manifest building
# ---------------------------------------------------------------------------

def parse_listing(lines) -> dict[str, tuple[str, list[AudioFile]]]:
    """Parse "size||path" lines into {brand_code: (top_name, [AudioFile...])}."""
    folders: dict[str, tuple[str, list[AudioFile]]] = {}
    for raw in lines:
        line = raw.rstrip("\n")
        if "||" not in line:
            continue
        size_s, path = line.split("||", 1)
        try:
            size = int(size_s)
        except ValueError:
            size = 0
        top = path.split("/", 1)[0]
        m = _BRAND_RE.match(top)
        if not m:
            continue
        brand = m.group(1).upper()
        title = m.group(2).strip()
        base = path.rsplit("/", 1)[-1]
        ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
        entry = folders.setdefault(brand, (title, []))
        if ext in AUDIO_EXT:
            entry[1].append(AudioFile(size=size, path=path, name=base, ext=ext))
    return folders


def build_manifest(lines) -> list[ClassifierResult]:
    folders = parse_listing(lines)
    results = [classify_folder(brand, title, files) for brand, (title, files) in folders.items()]
    results.sort(key=lambda r: _brand_num(r.brand_code))
    return results


def _brand_num(brand: str) -> int:
    m = re.search(r"(\d+)", brand)
    return int(m.group(1)) if m else 0


def safe_dst_name(brand_code: str, top_name: str, ext: str) -> str:
    """Destination filename mirroring the master-video naming so the
    ^NOMAD-(\\d+) master regex resolves it. Folder names are already
    filesystem-safe (Dropbox forbids '/'), but sanitise defensively."""
    base = f"{brand_code} - {top_name}".strip().rstrip(" .")
    base = re.sub(r"[/\x00-\x1f]", "_", base)
    return f"{base}.{ext}"


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

_CSV_FIELDS = ["brand_code", "artist", "title", "tier", "method", "chosen_ext",
               "n_audio", "n_candidates", "note", "chosen_path", "alt_candidates"]


def write_manifest_csv(results: list[ClassifierResult], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in results:
            row = asdict(r)
            row["alt_candidates"] = " ;; ".join(r.alt_candidates)
            w.writerow(row)


def write_manifest_json(results: list[ClassifierResult], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, indent=1)


def write_fetch_plan(results: list[ClassifierResult], path: str, dropbox_root: str) -> int:
    """Write a TSV of "<brand>\t<src_dropbox_path>\t<dst_filename>" for every
    auto-fetchable row. Returns the count."""
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for r in results:
            if not r.auto_fetch:
                continue
            top_name = f"{r.artist} - {r.title}".strip(" -") if r.artist else r.title
            # reconstruct exact top folder name from the chosen path (authoritative)
            folder = r.chosen_path.split("/", 1)[0]
            top = _BRAND_RE.match(folder)
            top_name = top.group(2).strip() if top else top_name
            dst = safe_dst_name(r.brand_code, top_name, r.chosen_ext)
            src = f"{dropbox_root.rstrip('/')}/{r.chosen_path}"
            f.write(f"{r.brand_code}\t{src}\t{dst}\n")
            n += 1
    return n


def summarize(results: list[ClassifierResult]) -> str:
    tiers = Counter(r.tier for r in results)
    methods = Counter(r.method for r in results)
    total = len(results)
    lines = [f"Folders classified: {total}", "", "Tier coverage:"]
    for t in ("HIGH", "MED", "LOW", "NO_SOURCE", "GAP"):
        c = tiers.get(t, 0)
        lines.append(f"  {t:9s} {c:5d}  ({100*c/total:.1f}%)" if total else f"  {t}: {c}")
    fetchable = sum(1 for r in results if r.auto_fetch)
    lines += ["", f"Auto-fetchable (HIGH+MED+LOW): {fetchable}", "", "Methods:"]
    for m, c in methods.most_common():
        lines.append(f"  {c:5d}  {m}")
    nums = sorted(_brand_num(r.brand_code) for r in results)
    if nums:
        missing = [n for n in range(1, max(nums) + 1) if n not in set(nums)]
        lines += ["", f"Numbering: NOMAD-0001..NOMAD-{max(nums):04d}; "
                      f"missing folder numbers: {len(missing)} -> {missing[:20]}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

DROPBOX_ROOT = "andrewdropboxfull:/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("listing", help="path to the 'size||path' recursive listing")
    ap.add_argument("--out-dir", default=".", help="directory for manifest outputs")
    ap.add_argument("--dropbox-root", default=DROPBOX_ROOT,
                    help="rclone remote:root prefixed onto chosen paths in the fetch plan")
    args = ap.parse_args(argv)

    with open(args.listing, encoding="utf-8", errors="replace") as f:
        results = build_manifest(f)

    import os
    os.makedirs(args.out_dir, exist_ok=True)
    csv_path = os.path.join(args.out_dir, "manifest.csv")
    json_path = os.path.join(args.out_dir, "manifest.json")
    fetch_path = os.path.join(args.out_dir, "fetch_plan.tsv")
    write_manifest_csv(results, csv_path)
    write_manifest_json(results, json_path)
    n = write_fetch_plan(results, fetch_path, args.dropbox_root)

    print(summarize(results))
    print(f"\nWrote:\n  {csv_path}\n  {json_path}\n  {fetch_path} ({n} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
