"""Songs whose catalogues spell the title two ways are found and offered as ONE song.

Real cases (2026-10-22 show):
- A singer typed "thanks for the memories". The good Fall Out Boy versions
  (SF, ASK, Zoom, Sound Choice, KV, Sing King...) are titled with the band's
  stylised "Thnks Fr Th Mmrs", so search found only NM/SD discs titled "Thanks
  For The Memories" and the KJ pick played a poor version.
- Lana Del Rey "Pawn Shop Blues" and "Pawnshop Blues" were two result groups;
  the singer picked the first, which held only a bare instrumental, while the
  lyric video sat in the other group.
"""
import types

import pytest

import catalog_mirror
import routes
from catalog import ExternalCatalog
from tests.test_catalog_mirror import _write_exports

SF = "/m/SF/SF-1102842 - Fall Out Boy - Thnks Fr Th Mmrs.zip"
ASK = "/m/ASK/ASK-043327 - Fall Out Boy - Thnks Fr Th Mmrs.zip"
NM = "/m/NM/NM2088-09 - Fall Out Boy - Thanks For The Memories.zip"
HOPE = "/m/NUKH/NUKH013-02 - Bob Hope - Thanks For The Memories.zip"
OTHER = "/m/X/X-01 - Someone - Thanks For Nothing.zip"


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


class TestCatalogVowelDropped:
    # Only the vowelled query expands: vowels can be dropped, not restored.
    # "thanks for the mem" isn't here either: "mem" -> "mm" is too short to
    # prefix-match; the finished word is.
    @pytest.mark.parametrize("q", [
        "thanks for the memories", "fall out boy thanks for the memories",
        "thanks for the memor",
    ])
    def test_finds_both_spellings(self, tmp_path, q):
        cat = _catalog(tmp_path, [SF, ASK, NM, HOPE, OTHER])
        paths = {r["path"] for r in cat.search(q)}
        assert {SF, ASK, NM} <= paths, q
        assert OTHER not in paths

    def test_single_word_query_not_expanded(self, tmp_path):
        cat = _catalog(tmp_path, ["/m/A-1 - Someone - Lv.zip",
                                  "/m/A-2 - Someone - Love.zip"])
        assert [r["title"] for r in cat.search("love")] == ["Love"]


class TestMirrorVowelDropped:
    def test_kn_search_finds_stylised_title(self, tmp_path, monkeypatch):
        import tests.test_catalog_mirror as tcm
        monkeypatch.setattr(tcm, "COMMUNITY_ITEMS", tcm.COMMUNITY_ITEMS + [
            {"Id": 3, "Artist": "Fall Out Boy", "Title": "Thnks Fr Th Mmrs",
             "Brand": "NOMAD", "Watch": "https://youtu.be/fob"}])
        community, full, dvb = _write_exports(tmp_path)
        db = str(tmp_path / "mirror.db")
        catalog_mirror.build_mirror_db(db, community, full, dvb)
        m = catalog_mirror.CatalogMirror({"catalog_mirror_db": db})
        try:
            assert m.is_usable()
            assert [r["title"] for r in
                    m.kn_search("fall out boy thanks for the memories")["community"]] \
                == ["Thnks Fr Th Mmrs"]
        finally:
            m.close()


class TestUnifiedSearch:
    def test_downloaded_stylised_title_found(self, tmp_path):
        dl = "/downloads/Fall Out Boy - Thnks Fr Th Mmrs [yt-HV-7retvH44].mp4"
        app = types.SimpleNamespace(
            catalog=_catalog(tmp_path, []), kj_config={},
            media=types.SimpleNamespace(index={dl: {
                "filename": dl.rsplit("/", 1)[1],
                "display_name": "Fall Out Boy - Thnks Fr Th Mmrs"}}))
        result = routes.unified_search("thanks for the memories", app, grouped=False)
        assert [r["path"] for r in result["local"]] == [dl]


def _local(path, artist, title):
    return {"path": path, "filename": path.rsplit("/", 1)[1],
            "artist": artist, "title": title, "format": "cdg+mp3"}


class TestGroupMerge:
    def test_vowel_dropped_title_joins_its_song(self):
        groups = routes._group_search_results(
            [_local(NM, "Fall Out Boy", "Thanks For The Memories"),
             _local(SF, "Fall Out Boy", "Thnks Fr Th Mmrs"),
             _local(ASK, "Fall Out Boy (MPX)", "Thnks Fr Th Mmrs"),
             _local(HOPE, "Bob Hope", "Thanks For The Memories")],
            [], query="thanks for the memories")
        by_artist = {g["artist"]: g for g in groups}
        assert len(groups) == 2
        fob = next(g for g in groups if "Fall Out Boy" in g["artist"])
        assert {v["local"]["path"] for v in fob["versions"]} == {NM, SF, ASK}
        assert {v["local"]["path"] for v in by_artist["Bob Hope"]["versions"]} == {HOPE}

    def test_spacing_variant_joins_its_song(self):
        a = "/dl/Lana Del Rey - Pawn Shop Blues [yt-a].mp4"
        b = "/dl/Lana Del Rey - Pawnshop Blues [yt-b].mp4"
        groups = routes._group_search_results(
            [_local(a, "Lana Del Rey", "Pawn Shop Blues"),
             _local(b, "Lana Del Rey", "Pawnshop Blues")], [], query="pawn shop blues")
        assert len(groups) == 1
        assert groups[0]["version_count"] == 2

    def test_kn_versions_merge_across_spellings(self):
        kn = [{"artist": "Fall Out Boy", "title": "Thnks Fr Th Mmrs",
               "tracks": [{"brand_code": "SC", "brand_name": "Sound Choice",
                           "youtube_url": "https://youtu.be/sc"}]}]
        groups = routes._group_search_results(
            [_local(NM, "Fall Out Boy", "Thanks For The Memories")], kn,
            query="thanks for the memories")
        assert len(groups) == 1
        assert {v["source"] for v in groups[0]["versions"]} == {"local", "kn"}

    @pytest.mark.parametrize("t1,t2", [
        ("Hello", "Hell"),                     # same consonants, real words
        ("Thanks For The Memories", "Thanks For The Memory"),
    ])
    def test_different_songs_stay_apart(self, t1, t2):
        groups = routes._group_search_results(
            [_local("/m/a.zip", "Someone", t1), _local("/m/b.zip", "Someone", t2)], [])
        assert len(groups) == 2

    def test_same_title_different_artist_stays_apart(self):
        groups = routes._group_search_results(
            [_local("/m/a.zip", "Fall Out Boy", "Thnks Fr Th Mmrs"),
             _local("/m/b.zip", "The Maine", "Thanks For The Memories")], [])
        assert len(groups) == 2
