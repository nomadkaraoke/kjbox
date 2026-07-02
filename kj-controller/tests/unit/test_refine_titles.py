from media_library import MediaLibraryStore
import scripts.refine_titles as rt


class FakeGen:
    def __init__(self, mapping):
        self.mapping = mapping

    def parse_titles(self, items):
        return [{"id": it["id"], **self.mapping.get(
            it["id"], {"artist": "", "title": "", "confidence": 0.0})}
            for it in items]


def _store():
    s = MediaLibraryStore(":memory:")
    s.upsert({"media_id": "yt-a", "source": "youtube",
              "artist": "Santeria", "title": "Sublime", "needs_review": 1,
              "confidence": 0.4, "raw_original_name": "Santeria - Sublime KaraFun.mp4"})
    return s


def test_dry_run_does_not_write():
    s = _store()
    gen = FakeGen({"yt-a": {"artist": "Sublime", "title": "Santeria", "confidence": 0.95}})
    out = rt.run_refine(s, gen, dry_run=True)
    assert out["refined"] == 1
    assert s.get("yt-a")["needs_review"] == 1  # unchanged on disk


def test_execute_applies_high_confidence():
    s = _store()
    gen = FakeGen({"yt-a": {"artist": "Sublime", "title": "Santeria", "confidence": 0.95}})
    out = rt.run_refine(s, gen, dry_run=False)
    row = s.get("yt-a")
    assert (row["artist"], row["title"]) == ("Sublime", "Santeria")
    assert row["needs_review"] == 0 and row["parse_method"] == "llm"


def test_offline_gen_none_is_noop():
    s = _store()

    class Offline:
        def parse_titles(self, items):
            return None

    out = rt.run_refine(s, Offline(), dry_run=False)
    assert out["offline"] is True and out["refined"] == 0
    assert s.get("yt-a")["needs_review"] == 1
