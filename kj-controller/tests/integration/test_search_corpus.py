"""End-to-end: every deterministic messy variant must retrieve its source row."""
import pytest
from catalog import ExternalCatalog
from tests.search_corpus import generate_variants, load_real_samples

ROWS = [
    ("Simon & Garfunkel", "Sound Of Silence"),
    ("Queen", "Bohemian Rhapsody"),
    ("Beyoncé", "Halo"),
    ("Twenty One Pilots", "Stressed Out"),
    # AC/DC is stored as "ACDC" on disk: POSIX forbids '/' in filenames, so
    # real karaoke disc releases drop or omit the slash in the filename.
    # The normalization pipeline handles "ACDC" correctly via FTS.
    ("ACDC", "T.N.T."),
    ("Florence + The Machine", "Dog Days Are Over"),
    ("Earth Wind and Fire", "September"),
    ("Guns N' Roses", "Paradise City"),
    ("3 Doors Down", "Kryptonite"),
]


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    d = tmp_path_factory.mktemp("corpus")
    listing = d / "list.txt"
    lines = [f"/m/ID{i} - {a} - {t}.zip" for i, (a, t) in enumerate(ROWS)]
    listing.write_text("\n".join(lines) + "\n")
    cat = ExternalCatalog({}, db_path=str(d / "external_media.db"))
    cat.init_schema()
    cat.build_from_file_list(str(listing))
    return cat


def _hits(cat, query, k=10):
    return cat.search(query, limit=k)


@pytest.mark.parametrize("artist,title", ROWS)
def test_all_variants_recall(catalog, artist, title):
    failures = []
    for query, note in generate_variants(artist, title):
        results = _hits(catalog, query)
        if not any(r["title"] == title and r["artist"] == artist for r in results):
            failures.append((query, note))
    assert not failures, f"missed variants for {artist} - {title}: {failures}"


def test_real_samples_if_present(catalog):
    samples = load_real_samples()
    if not samples:
        pytest.skip("no real samples committed yet")
    misses = []
    for s in samples:
        results = _hits(catalog, s["query"])
        if not any(r["title"] == s["expect_title"] and r["artist"] == s["expect_artist"]
                   for r in results):
            misses.append(s["query"])
    assert len(misses) <= len(samples) * 0.1, f"too many real-sample misses: {misses}"


def test_metrics_harness_runs():
    import subprocess, sys, os
    # __file__ is kj-controller/tests/integration/test_search_corpus.py
    # go up three levels: integration/ -> tests/ -> kj-controller/
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = subprocess.check_output(
        [sys.executable, os.path.join(root, "scripts", "search_metrics.py")],
        text=True, cwd=root,
    )
    assert "recall@10" in out
