#!/usr/bin/env python3
"""Report recall@K and per-variant-type breakdown for the search corpus.

Usage:
  python scripts/search_metrics.py            # synthetic corpus over sample rows

Prints recall@1/@5/@10 overall and grouped by variant note. Use to tune
catalog.FUZZY_SCORE_CUTOFF.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import ExternalCatalog                      # noqa: E402
from tests.search_corpus import generate_variants        # noqa: E402

SAMPLE_ROWS = [
    ("Simon & Garfunkel", "Sound Of Silence"),
    ("Queen", "Bohemian Rhapsody"),
    ("Beyoncé", "Halo"),
    ("Twenty One Pilots", "Stressed Out"),
    ("Florence + The Machine", "Dog Days Are Over"),
    ("Earth Wind and Fire", "September"),
    ("Guns N' Roses", "Paradise City"),
    ("3 Doors Down", "Kryptonite"),
]


def _build(tmpdir):
    listing = os.path.join(tmpdir, "list.txt")
    with open(listing, "w", encoding="utf-8") as fh:
        for i, (a, t) in enumerate(SAMPLE_ROWS):
            fh.write(f"/m/ID{i} - {a} - {t}.zip\n")
    cat = ExternalCatalog({}, db_path=os.path.join(tmpdir, "external_media.db"))
    cat.init_schema()
    cat.build_from_file_list(listing)
    return cat


def _rank(results, artist, title):
    for idx, r in enumerate(results):
        if r.get("artist") == artist and r.get("title") == title:
            return idx + 1
    return None


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        cat = _build(d)
        by_note = defaultdict(lambda: [0, 0])
        ranks = []
        for artist, title in SAMPLE_ROWS:
            for query, note in generate_variants(artist, title):
                results = cat.search(query, limit=10)
                rank = _rank(results, artist, title)
                ranks.append(rank)
                by_note[note][1] += 1
                if rank is not None and rank <= 10:
                    by_note[note][0] += 1

        def recall_at(k):
            ok = sum(1 for r in ranks if r is not None and r <= k)
            return ok / len(ranks) if ranks else 0.0

        print(f"queries: {len(ranks)}")
        print(f"recall@1:  {recall_at(1):.3f}")
        print(f"recall@5:  {recall_at(5):.3f}")
        print(f"recall@10: {recall_at(10):.3f}")
        print("by variant type (hits@10 / total):")
        for note, (hit, tot) in sorted(by_note.items()):
            print(f"  {note:18s} {hit}/{tot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
