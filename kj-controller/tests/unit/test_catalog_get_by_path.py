# kj-controller/tests/unit/test_catalog_get_by_path.py
import os
import unicodedata

from catalog import ExternalCatalog, parse_karaoke_filename


def _catalog_with(tmp_path, paths):
    cat = ExternalCatalog({"external_catalog_db": str(tmp_path / "cat.db")})
    cat.init_schema()
    conn = cat._get_conn()
    for p in paths:
        fname = os.path.basename(p)
        disc, artist, title = parse_karaoke_filename(fname)
        conn.execute(
            "INSERT INTO media (path, filename, folder, disc_id, artist, title, format) "
            "VALUES (?,?,?,?,?,?,?)",
            (p, fname, os.path.dirname(p), disc, artist, title, "zip"),
        )
    conn.commit()
    return cat


def test_get_by_path_exact(tmp_path):
    p = "/media/nomad/Nomad4TBOne/Discs/SC8123-04 - ABBA - SOS.zip"
    cat = _catalog_with(tmp_path, [p])
    row = cat.get_by_path(p)
    assert row and row["artist"] == "ABBA" and row["title"] == "SOS"


def test_get_by_path_nfc_nfd_variants(tmp_path):
    # Catalog built from an NFD (macOS-era) file list; runtime path is NFC.
    nfd = unicodedata.normalize(
        "NFD", "/media/nomad/Nomad4TBOne/Discs/K1 - Céline Dion - Pour que tu m'aimes.zip")
    cat = _catalog_with(tmp_path, [nfd])
    nfc = unicodedata.normalize("NFC", nfd)
    assert nfc != nfd  # the test is vacuous if the path has no composing chars
    row = cat.get_by_path(nfc)
    assert row and "Dion" in row["artist"]


def test_get_by_path_miss_and_unavailable(tmp_path):
    cat = _catalog_with(tmp_path, ["/media/nomad/Nomad4TBOne/Discs/X1 - A - B.zip"])
    assert cat.get_by_path("/media/nomad/Nomad4TBOne/nope.zip") is None
    empty = ExternalCatalog({"external_catalog_db": str(tmp_path / "absent.db")})
    assert empty.get_by_path("/media/nomad/x.zip") is None  # db missing -> None, no raise
    assert cat.get_by_path("") is None
    assert cat.get_by_path(None) is None
