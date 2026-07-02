# kj-controller/scripts/refine_titles.py
"""Batch-refine needs_review media_library rows via the gen parse endpoint.

DB-ONLY: updates artist/title/needs_review (never renames files — that is the
Phase-4 migration). Dry-run by default. Offline (gen unavailable) -> no-op.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import load_config  # noqa: E402
from media_library import MediaLibraryStore  # noqa: E402


def run_refine(store, gen_client, *, threshold=0.75, batch_size=100, dry_run=True):
    """Refine every needs_review=1 row. Returns a summary dict. Never raises on a
    gen outage: a None batch result short-circuits (offline=True)."""
    rows = store.list_records(needs_review=1)
    total = len(rows)
    refined = unchanged = 0
    offline = False
    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        items = [{"id": r["media_id"],
                  "filename": r.get("raw_original_name") or r.get("file_path") or "",
                  "source": r.get("source")} for r in batch]
        results = gen_client.parse_titles(items) if gen_client else None
        if results is None:
            offline = True
            break
        by_id = {str(r.get("id")): r for r in results}
        for r in batch:
            llm = by_id.get(r["media_id"]) or {}
            artist = (llm.get("artist") or "").strip()
            title = (llm.get("title") or "").strip()
            conf = llm.get("confidence")
            if not artist and not title:
                unchanged += 1
                continue
            refined += 1
            if not dry_run:
                store.apply_parse(r["media_id"], artist, title, conf, threshold)
    return {"total": total, "refined": refined, "unchanged": unchanged, "offline": offline}


def main():
    ap = argparse.ArgumentParser(description="Refine needs_review media rows via gen LLM")
    ap.add_argument("--execute", action="store_true", help="apply changes (default dry-run)")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--batch-size", type=int, default=100)
    args = ap.parse_args()

    cfg = load_config()
    threshold = args.threshold if args.threshold is not None else float(
        cfg.get("parse_confidence_threshold", 0.75) or 0.75)
    store = MediaLibraryStore(cfg.get("media_db_path"))
    gen_client = None
    url, tok = cfg.get("gen_api_url", ""), cfg.get("gen_api_token", "")
    if url and tok:
        from gen_client import GenClient
        gen_client = GenClient(url, tok)
    out = run_refine(store, gen_client, threshold=threshold,
                     batch_size=args.batch_size, dry_run=not args.execute)
    print(("DRY-RUN " if not args.execute else "") + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
