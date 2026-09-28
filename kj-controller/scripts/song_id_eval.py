#!/usr/bin/env python3
"""Score the on-device song identifier against the labelled test set.

Usage: python scripts/song_id_eval.py path/to/song_id.db [--verbose] [--synthetic N | --frozen]

--synthetic N: instead of the labelled set, sample N popular karaoke songs from the
index and generate "drunk" variants (typos, title only, artist fragment, dropped
apostrophes, doubled/dropped letters, keyboard slips). A held-out check: the
matcher was never tuned on these.

Per case:
  confident-correct  — auto-applied the right song                 (goal)
  confident-WRONG    — auto-applied a wrong song                   (must be ~0)
  cand-correct       — right song in the "Which one?" candidates
  cand-miss          — candidates shown, right song not among them
  none               — no local answer (→ Gemini fallback)
For negatives (no artist/title), "none" or "cand" is fine; confident is WRONG.
"""
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from song_identify import SongIdentifier  # noqa: E402
from text_normalize import normalize  # noqa: E402

EVAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tests", "fixtures", "song_id_eval.jsonl")


def _key(s):
    return normalize(s).replace(" ", "")


_CREDIT_SPLIT_RE = re.compile(r"\s*(?:,|&|\+|/|\bfeaturing\b|\bfeat\.?|\bft\.?|\bx\b|\band\b|\bwith\b)\s*", re.I)
_VERSION_RE = re.compile(r"\s*[\(\[].*$|\s+-\s+.*$")


def _credits(artist):
    return {_key(p) for p in _CREDIT_SPLIT_RE.split(artist or "") if _key(p)}


def _is(case, m):
    """Same song as a person would judge it: same title (ignoring "(feat. …)" /
    "- Remastered" suffixes) and at least one credited artist in common
    ("Cashmere Cat feat. Ariana Grande" ~ "Ariana Grande")."""
    if m is None:
        return False
    if case["title"] and _key(_VERSION_RE.sub("", case["title"])) != _key(_VERSION_RE.sub("", m["title"])):
        return False
    if case["artist"] and not (_credits(case["artist"]) & _credits(m["artist"])):
        return False
    return True


_KEYS = "qwertyuiop asdfghjkl zxcvbnm"
_NEAR = {c: (_KEYS[i - 1] if i else "") + (_KEYS[i + 1] if i + 1 < len(_KEYS) else "")
         for i, c in enumerate(_KEYS) if c != " "}


def _typo(word, rng):
    if len(word) < 4:
        return word
    i = rng.randrange(1, len(word) - 1)
    kind = rng.choice(["drop", "double", "swap", "near"])
    if kind == "drop":
        return word[:i] + word[i + 1:]
    if kind == "double":
        return word[:i] + word[i] + word[i:]
    if kind == "swap":
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    near = _NEAR.get(word[i].lower(), "").replace(" ", "")
    return word[:i] + (rng.choice(near) if near else word[i]) + word[i + 1:]


def _typo_longest(text, rng):
    words = text.split()
    if not words:
        return text
    i = max(range(len(words)), key=lambda k: len(words[k]))
    words[i] = _typo(words[i], rng)
    return " ".join(words)


def synthetic_cases(db_path, n, seed=7):
    import random
    import sqlite3
    rng = random.Random(seed)
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    # Seeded sample (SQLite's random() ignores the seed): same songs every run for a
    # given index, so before/after comparisons are like for like.
    pool = db.execute("SELECT artist, title FROM songs WHERE karaoke = 1 AND pop >= 55 "
                      "AND length(title) BETWEEN 4 AND 40 ORDER BY lower(artist), lower(title)").fetchall()
    songs = rng.sample(pool, min(n, len(pool)))
    cases = []
    for artist, title in songs:
        t, a = title.lower(), artist.lower()
        a_frag = rng.choice(a.split()) if a.split() else a
        for q, kind in [
            (t, "syn-title-only"),
            (f"{t} {a}", "syn-exact"),
            (f"{a} {_typo_longest(t, rng)}", "syn-typo-title"),
            (f"{_typo_longest(a, rng)} {t}", "syn-typo-artist"),
            (f"{t} {a_frag}", "syn-artist-fragment"),
            (f"{_typo_longest(t, rng)} {a_frag}".replace("'", ""), "syn-fragment+typo"),
        ]:
            # Title-only keeps the sampled artist as the expected answer: a confident
            # answer naming ANOTHER artist's same-title song is wrong for the singer
            # (it would pre-fill the wrong artist), so it must not score as correct.
            cases.append({"q": q, "artist": artist, "title": title, "kind": kind, "source": "synthetic"})
    return cases


def main():
    db = sys.argv[1]
    verbose = "--verbose" in sys.argv
    ident = SongIdentifier(db)
    if "--synthetic" in sys.argv:
        cases = synthetic_cases(db, int(sys.argv[sys.argv.index("--synthetic") + 1]))
    elif "--frozen" in sys.argv:
        # The frozen held-out set (tests/fixtures/song_id_synthetic.jsonl, 900 cases
        # generated once): compares indexes/matcher versions like for like.
        cases = [json.loads(line) for line in open(EVAL.replace("song_id_eval", "song_id_synthetic"),
                                                    encoding="utf-8") if line.strip()]
    else:
        cases = [json.loads(line) for line in open(EVAL, encoding="utf-8") if line.strip()]
    ident.identify("warm up")
    by_kind = defaultdict(Counter)
    total = Counter()
    times = []
    for c in cases:
        t0 = time.perf_counter()
        r = ident.identify(c["q"])
        times.append((time.perf_counter() - t0) * 1000)
        negative = not c["title"] and not c["artist"]
        if r["status"] == "confident":
            outcome = "confident-WRONG" if negative or not _is(c, r["best"]) else "confident-correct"
        elif r["status"] == "candidates":
            outcome = "cand-ok" if negative else (
                "cand-correct" if any(_is(c, m) for m in r["candidates"]) else "cand-miss")
        else:
            outcome = "none"
        by_kind[c["kind"]][outcome] += 1
        total[outcome] += 1
        if verbose or outcome in ("confident-WRONG", "cand-miss", "none"):
            b = r["best"]
            got = f'{b["artist"]} — {b["title"]} ({b["score"]} {b.get("detail")})' if b else "-"
            print(f'{outcome:18} [{c["kind"]}] {c["q"]!r} → {got}   want: {c["artist"]} — {c["title"]}')
    print()
    for kind, cnt in sorted(by_kind.items()):
        print(f"{kind:22} " + "  ".join(f"{k}={v}" for k, v in sorted(cnt.items())))
    n = len(cases)
    print(f"\nTOTAL {n}: " + "  ".join(f"{k}={v} ({v / n:.0%})" for k, v in sorted(total.items())))
    print(f"latency ms: p50={statistics.median(times):.0f} p95={sorted(times)[int(len(times) * .95) - 1]:.0f}"
          f" max={max(times):.0f}")


if __name__ == "__main__":
    main()
