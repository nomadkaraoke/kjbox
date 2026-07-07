"""TDD tests for oracle_run resumable driver: folder_for_brand & load_done_brands."""
import csv
import os
import tempfile

from oracle_run import folder_for_brand, load_done_brands


def test_folder_for_brand_globs_tracks_organized(tmp_path):
    """folder_for_brand globs Tracks-Organized for matching brand folder."""
    root = tmp_path / "Tracks-Organized"
    (root / "NOMAD-0100 - Idlewild - Little Discourage").mkdir(parents=True)
    assert os.path.basename(folder_for_brand(str(root), "NOMAD-0100")) == \
        "NOMAD-0100 - Idlewild - Little Discourage"
    assert folder_for_brand(str(root), "NOMAD-9999") is None


def test_load_done_brands_reads_csv_column(tmp_path):
    """load_done_brands reads brand column from CSV into a set."""
    csv_path = tmp_path / "results.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["brand", "verdict"])
        w.writeheader()
        w.writerow({"brand": "NOMAD-0100", "verdict": "confirmed"})
        w.writerow({"brand": "NOMAD-0200", "verdict": "no_source"})
    assert load_done_brands(str(csv_path)) == {"NOMAD-0100", "NOMAD-0200"}


def test_load_done_brands_returns_empty_on_missing_file():
    """load_done_brands returns empty set if CSV doesn't exist."""
    assert load_done_brands("/nonexistent/path.csv") == set()
