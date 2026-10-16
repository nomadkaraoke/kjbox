"""Run-together queries ("overthinking") find split titles ("(Over)Thinking"),
and an MP4 outranks a CD+G zip of the same brand.

Real case (2026-10-16): NOMAD-0147 Nia Archives "(Over)Thinking" exists as a
CD+G zip titled "Over, Thinking" plus two MP4s. Typing "Nia Archives
Overthinking" found none of them (both titles index as the two words "over" +
"thinking"), and "over thinking" marked the CD+G zip Best because nothing broke
the same-brand tie.
"""
import types

import pytest

import catalog_mirror
import routes
from catalog import ExternalCatalog
from tests.test_catalog_mirror import _write_exports

MASTER = "/m/Andrew The Nomad MP4"
ZIP = f"{MASTER}/CDG/NOMAD-0100-0199/NOMAD-0147 - Nia Archives - Over, Thinking.zip"
MP4 = f"{MASTER}/MP4/NOMAD 0001-0250/NOMAD-0147 - Nia Archives - (Over)Thinking.mp4"
DL = "/downloads/NOMAD-720p/NOMAD-0147 - Nia Archives - (Over)Thinking.mp4"


def _catalog(tmp_path, paths):
    file_list = tmp_path / "list.txt"
    file_list.write_text("\n".join(paths) + "\n", encoding="utf-8")
    cat = ExternalCatalog({}, db_path=str(tmp_path / "external_media.db"))
    cat.init_schema()
    cat.build_from_file_list(str(file_list))
    return cat


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(routes.karaoke_nerds, "search", lambda *a, **k: [])
    monkeypatch.setattr(routes.divebar, "search", lambda *a, **k: [])


class TestCatalogJoinedWords:
    @pytest.mark.parametrize("q", [
        "overthinking", "Nia Archives Overthinking", "nia archives overth",
        "over thinking", "(Over)Thinking",
    ])
    def test_finds_both_spellings(self, tmp_path, q):
        cat = _catalog(tmp_path, [ZIP, MP4])
        assert {r["path"] for r in cat.search(q)} == {ZIP, MP4}, q

    def test_artist_joined_too(self, tmp_path):
        cat = _catalog(tmp_path, ["/m/SC1 - AC-DC - Thunderstruck.zip",
                                  "/m/SC2 - Blink 182 - All the Small Things.zip"])
        assert [r["title"] for r in cat.search("acdc thunderstruck")] == ["Thunderstruck"]
        assert [r["title"] for r in cat.search("blink182")] == ["All the Small Things"]

    def test_joined_word_does_not_overmatch(self, tmp_path):
        cat = _catalog(tmp_path, [ZIP, "/m/X1 - Someone - Over the Rainbow.zip"])
        assert [r["path"] for r in cat.search("overthinking")] == [ZIP]

    def test_rebuild_fts_indexes_joined_words(self, tmp_path):
        cat = _catalog(tmp_path, [MP4])
        conn = cat._get_conn()
        conn.execute("UPDATE catalog_meta SET value='3' WHERE key='normalizer_version'")
        conn.commit()
        assert cat.index_is_stale() is True  # pre-index_text stamp
        cat.rebuild_fts()
        assert cat.index_is_stale() is False
        assert [r["path"] for r in cat.search("overthinking")] == [MP4]


class TestMirrorJoinedWords:
    def test_kn_and_divebar_find_run_together_query(self, tmp_path, monkeypatch):
        import tests.test_catalog_mirror as tcm
        monkeypatch.setattr(tcm, "COMMUNITY_ITEMS", tcm.COMMUNITY_ITEMS + [
            {"Id": 3, "Artist": "Nia Archives", "Title": "(Over)Thinking",
             "Brand": "NOMAD", "Watch": "https://youtu.be/ot"}])
        monkeypatch.setattr(tcm, "DIVEBAR_ROWS", tcm.DIVEBAR_ROWS + [
            {"file_id": "ot", "brand": "Nomad Karaoke", "brand_code": "NOMAD",
             "artist": "Nia Archives", "title": "Over, Thinking",
             "filename": "z.zip", "format": "zip", "file_size": 5,
             "drive_path": "NOMAD/z.zip", "in_gcs": True}])
        community, full, dvb = _write_exports(tmp_path)
        db = str(tmp_path / "mirror.db")
        catalog_mirror.build_mirror_db(db, community, full, dvb)
        m = catalog_mirror.CatalogMirror({"catalog_mirror_db": db})
        try:
            assert m.is_usable()
            assert [r["title"] for r in m.kn_search("nia archives overthinking")["community"]] \
                == ["(Over)Thinking"]
            assert [r["file_id"] for r in m.divebar_search("overthinking")] == ["ot"]
            # No cross-field junk pair ("archivesover") in the index.
            assert m.divebar_search("archivesover") == []
        finally:
            m.close()


class TestUnifiedSearch:
    def _app(self, tmp_path, catalog_paths, index):
        cat = _catalog(tmp_path, catalog_paths)
        return types.SimpleNamespace(
            catalog=cat, media=types.SimpleNamespace(index=index), kj_config={})

    def test_downloaded_file_found_by_run_together_query(self, tmp_path):
        app = self._app(tmp_path, [], {DL: {
            "filename": DL.rsplit("/", 1)[1],
            "display_name": "Nia Archives - (Over)Thinking"}})
        result = routes.unified_search("overthinking", app, grouped=False)
        assert [r["path"] for r in result["local"]] == [DL]

    def test_mp4_ranks_above_same_brand_cdg(self, tmp_path):
        """The screenshot case: catalog zip + catalog MP4 + downloaded MP4,
        all NOMAD/local — the best (lowest priority_rank) must be an MP4."""
        app = self._app(tmp_path, [ZIP, MP4], {DL: {
            "filename": DL.rsplit("/", 1)[1],
            "display_name": "Nia Archives - (Over)Thinking"}})
        for q in ("nia archives over thinking", "Nia Archives Overthinking"):
            local = routes.unified_search(q, app, grouped=False)["local"]
            assert {r["path"] for r in local} == {ZIP, MP4, DL}, q
            best = sorted(local, key=lambda r: r["priority_rank"])[0]  # stable, like the UI
            assert best["path"] == MP4, q
            zip_row = next(r for r in local if r["path"] == ZIP)
            assert all(zip_row["priority_rank"] > r["priority_rank"]
                       for r in local if r["path"] != ZIP)


class TestGroupedDisplayName:
    def test_singer_group_named_after_best_local_version(self):
        """Locals win display-name arbitration; the BEST local (the MP4) names
        the song, not whichever file the catalog happened to return first."""
        zip_row = {"path": ZIP, "disc_id": "NOMAD-0147", "artist": "Nia Archives",
                   "title": "Over, Thinking", "format": "cdg+mp3"}
        mp4_row = {"path": MP4, "disc_id": "NOMAD-0147", "artist": "Nia Archives",
                   "title": "(Over)Thinking", "format": "mp4"}
        groups = routes._group_search_results([zip_row, mp4_row], [], query="overthinking")
        assert len(groups) == 1
        assert groups[0]["title"] == "(Over)Thinking"
        assert groups[0]["versions"][0]["local"]["path"] == MP4
