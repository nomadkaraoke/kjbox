"""Deterministic messy-query corpus for search testing.

generate_variants(artist, title) yields (query, note) pairs that SHOULD all
retrieve the (artist, title) row. Deterministic: no randomness.
load_real_samples() returns committed real-world rows (hybrid layer, D1).
"""
import json
import os

_FIX = os.path.join(os.path.dirname(__file__), "fixtures", "real_rotation_samples.json")

_WORD_FOR_DIGIT = {"0": "zero", "1": "one", "2": "two", "3": "three",
                   "4": "four", "5": "five", "6": "six", "7": "seven",
                   "8": "eight", "9": "nine"}


def _digits_to_words(s):
    return " ".join(_WORD_FOR_DIGIT.get(ch, ch) for ch in s) if s.isdigit() else s


def generate_variants(artist, title):
    a, t = artist.strip(), title.strip()
    base = f"{t} - {a}"
    out = [(base, "base")]
    if "&" in a:
        out.append((base.replace("&", "and"), "amp->and"))
    if " and " in a.lower():
        out.append((base.lower().replace(" and ", " & "), "and->amp"))
    if "'" in base or "’" in base:
        out.append((base.replace("'", "").replace("’", ""), "drop-apostrophe"))
    toks = base.split()
    if any(tok.isdigit() for tok in toks):
        out.append((" ".join(_digits_to_words(tok) for tok in toks), "digit->word"))
    import unicodedata
    ascii_base = "".join(
        c for c in unicodedata.normalize("NFD", base)
        if unicodedata.category(c) != "Mn"
    )
    if ascii_base != base:
        out.append((ascii_base, "strip-diacritics"))
    out.append((t, "title-only"))
    long_toks = sorted([w for w in toks if len(w) >= 6], key=len, reverse=True)
    if long_toks:
        w = long_toks[0]
        typo = w[:3] + w[4:]
        out.append((base.replace(w, typo, 1), "typo"))
    seen, dedup = set(), []
    for q, note in out:
        if q and q not in seen:
            seen.add(q)
            dedup.append((q, note))
    return dedup


def load_real_samples():
    """Return [{'query':..., 'expect_artist':..., 'expect_title':...}, ...]."""
    try:
        with open(_FIX, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
