"""Regression fixture for the deterministic playability checker.

The fixed checker must agree with the ffmpeg ground-truth on the 166-file manual
review set (June 2026): flag ONLY the genuinely-broken files (truncated
downloads, no-audio/no-stream, corrupt zips) and never a playable one.

The media files are large and local-only, so this test is skipped unless the
review set is present. Point it at the review set with PLAYABILITY_REVIEW_SET
(defaults to ~/playability-review); the expected verdicts live in the committed
manifest tests/fixtures/playability_regression_manifest.tsv.

See docs/archive/2026-06-30-playability-checker-reliability-design.md.
"""
import csv
import os

import pytest

from playability import PlayabilityChecker

REVIEW_SET = os.environ.get(
    "PLAYABILITY_REVIEW_SET", os.path.expanduser("~/playability-review")
)
MANIFEST = os.path.join(
    os.path.dirname(__file__), os.pardir, "fixtures",
    "playability_regression_manifest.tsv",
)


def _load_manifest():
    with open(MANIFEST, newline="") as fh:
        return [
            (r["relpath"], r["expected_ok"] == "1", r["category"])
            for r in csv.DictReader(fh, delimiter="\t")
        ]


@pytest.mark.skipif(
    not os.path.isdir(REVIEW_SET),
    reason=f"playability review set not present at {REVIEW_SET}",
)
def test_fixed_checker_matches_ground_truth():
    chk = PlayabilityChecker(config={})
    false_positives, false_negatives, total = [], [], 0
    for rel, expected_ok, category in _load_manifest():
        path = os.path.join(REVIEW_SET, rel)
        if not os.path.isfile(path):
            continue
        total += 1
        # Decode-only verdict (renderers=()) — render is diagnostic, not a gate.
        got_ok = bool(chk.check(path, renderers=(), depth="deep").verdict.get("overall_ok"))
        if got_ok and not expected_ok:
            false_negatives.append((rel, category))   # passed a broken file
        elif not got_ok and expected_ok:
            false_positives.append((rel, category))    # flagged a playable file

    assert total > 0, "review set present but no manifest files found on disk"
    # Zero false positives is the headline requirement — never flag a playable file.
    assert false_positives == [], f"flagged playable files: {false_positives}"
    # And it must still catch the genuinely-broken ones.
    assert false_negatives == [], f"passed broken files: {false_negatives}"
