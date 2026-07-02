import os
from unittest.mock import patch

from media import MediaIndex
from media_library import MediaLibraryStore


def _mi(tmp_path, gen_client=None):
    cfg = {"download_folder": str(tmp_path), "media_folders": [str(tmp_path)],
           "media_index_path": str(tmp_path / "i.json"),
           "parse_confidence_threshold": 0.75}
    mi = MediaIndex(cfg, media_library=MediaLibraryStore(":memory:"))
    mi.gen_client = gen_client
    return mi


# --- B4: _finalize_download_identity ---

def test_finalize_writes_slug_into_source_folder_offline(tmp_path):
    mi = _mi(tmp_path)  # no gen_client -> deterministic
    src = tmp_path / "raw.mp4"
    src.write_bytes(b"\x00" * 16)
    final, display, media_id = mi._finalize_download_identity(
        str(src), source="youtube", source_ref="UM1XiyBmhMz",
        artist_hint="Bella Kay", title_hint="iloveit", channel="Sing King",
        raw_name="-UM1XiyBmhMz__Sing King__Bella Kay - iloveit.mp4", ext=".mp4")
    assert media_id == "yt-UM1XiyBmhMz"
    assert os.path.dirname(final).endswith(os.sep + "youtube")
    assert final.endswith("[yt-UM1XiyBmhMz].mp4")
    assert os.path.exists(final) and not os.path.exists(src)
    row = mi.media_library.get("yt-UM1XiyBmhMz")
    assert row["file_path"] == os.path.realpath(final)
    assert row["needs_review"] == 1  # offline deterministic


def test_finalize_uses_llm_when_gen_client_present(tmp_path):
    class FakeGen:
        def parse_titles(self, items):
            return [{"id": items[0]["id"], "artist": "Sublime",
                     "title": "Santeria", "confidence": 0.95}]
    mi = _mi(tmp_path, gen_client=FakeGen())
    src = tmp_path / "raw.mp4"
    src.write_bytes(b"\x00" * 16)
    final, display, media_id = mi._finalize_download_identity(
        str(src), source="youtube", source_ref="ABCDEFGHIJK",
        artist_hint="Santeria", title_hint="Sublime", channel="KaraFun",
        raw_name="Santeria - Sublime _ KaraFun.mp4", ext=".mp4")
    row = mi.media_library.get("yt-ABCDEFGHIJK")
    assert (row["artist"], row["title"]) == ("Sublime", "Santeria")
    assert row["needs_review"] == 0 and row["parse_method"] == "llm"
    assert display == "Sublime - Santeria"


def test_finalize_survives_llm_exception(tmp_path):
    class BoomGen:
        def parse_titles(self, items):
            raise RuntimeError("offline")
    mi = _mi(tmp_path, gen_client=BoomGen())
    src = tmp_path / "raw.mp4"
    src.write_bytes(b"\x00" * 16)
    final, display, media_id = mi._finalize_download_identity(
        str(src), source="community", source_ref="WTF-abc123",
        artist_hint="Queen", title_hint="Bohemian Rhapsody", channel=None,
        raw_name="WTF - Queen - Bohemian Rhapsody.mp4", ext=".mp4")
    assert media_id == "db-WTF-abc123"
    assert os.path.exists(final)
    assert os.path.dirname(final).endswith(os.sep + "community")


# --- B5: download methods route through the finalizer ---

def test_download_from_url_gen_source_lands_in_gen_folder(tmp_path):
    mi = _mi(tmp_path)

    def fake_http(url, path):
        if path:
            with open(path, "wb") as f:
                f.write(b"\x00" * 16)
        return None

    with patch.object(mi, "_http_download", side_effect=fake_http), \
         patch("media._gate_playable") as gate:
        gate.return_value.verdict = {"overall_ok": True, "reasons": []}
        real, display = mi.download_from_url(
            "https://x/y.mp4", filename="GEN-1a2b3c4d - Cher - Believe.mp4",
            source="gen", source_ref="1a2b3c4d", artist="Cher", title="Believe")
    assert os.sep + "gen" + os.sep in real
    assert real.endswith("[gen-1a2b3c4d].mp4")
    assert mi.media_library.get("gen-1a2b3c4d")["source"] == "gen"


def test_download_from_url_community_lands_in_community_folder(tmp_path):
    mi = _mi(tmp_path)

    def fake_http(url, path):
        if path:
            with open(path, "wb") as f:
                f.write(b"\x00" * 16)
        return None

    with patch.object(mi, "_http_download", side_effect=fake_http), \
         patch("media._gate_playable") as gate:
        gate.return_value.verdict = {"overall_ok": True, "reasons": []}
        real, display = mi.download_from_url(
            "https://x/y.mp4", filename="WTF - Queen - Bohemian.mp4",
            source="community", source_ref="WTF-drivefileid123",
            artist="Queen", title="Bohemian Rhapsody")
    assert os.sep + "community" + os.sep in real
    assert mi.media_library.get("db-WTF-drivefileid123") is not None


def test_download_from_url_no_source_ref_is_upload(tmp_path):
    mi = _mi(tmp_path)

    def fake_http(url, path):
        if path:
            with open(path, "wb") as f:
                f.write(b"\x00" * 16)
        return None

    with patch.object(mi, "_http_download", side_effect=fake_http), \
         patch("media._gate_playable") as gate:
        gate.return_value.verdict = {"overall_ok": True, "reasons": []}
        real, display = mi.download_from_url(
            "https://x/y.mp4", filename="Some Upload.mp4", source_ref=None)
    assert os.sep + "upload" + os.sep in real
    rows = mi.media_library.list_records(source="upload")
    assert len(rows) == 1 and rows[0]["media_id"].startswith("up-")
