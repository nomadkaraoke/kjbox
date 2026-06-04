#!/usr/bin/env python3
"""Rebuild the catalog FTS + trigram indexes with the current normalizer.

Usage: python scripts/reindex_catalog.py [/path/to/external_media.db]
Run after deploying a normalizer change (NORMALIZER_VERSION bump).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_config           # noqa: E402
from catalog import ExternalCatalog      # noqa: E402


def main():
    cfg = load_config()
    db_path = sys.argv[1] if len(sys.argv) > 1 else None
    cat = ExternalCatalog(cfg, db_path=db_path)
    if not cat.is_available():
        print("Catalog DB not found or empty; nothing to reindex.")
        return 1

    def progress(done, total):
        if done % 50000 == 0 or done == total:
            print(f"  reindexed {done}/{total}")

    print("Rebuilding FTS + trigram indexes...")
    total = cat.rebuild_fts(callback=progress)
    print(f"Done. {total} rows reindexed; stale={cat.index_is_stale()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
