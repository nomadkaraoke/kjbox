"""Unit tests for the YouTube circuit breaker (youtube_breaker.py)."""

import youtube_breaker
from youtube_breaker import YouTubeBreaker, drop_youtube_only, is_youtube_only


class FakeStore:
    def __init__(self):
        self.meta = {}

    def _get_meta(self, key, default=None):
        return self.meta.get(key, default)

    def _set_meta(self, key, value):
        self.meta[key] = value


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeMedia:
    def __init__(self, result, last_error=None):
        self.result = result
        self.urls = []
        self._last_error = last_error

    def download_video(self, url):
        self.urls.append(url)
        return self.result


LOCAL = {"source": "local", "local": {"path": "/m/a.mp4"}}
DIVEBAR = {"source": "kn", "kn": {"divebar": {"file_id": "F1"}, "youtube_url": "https://youtu.be/d"}}
YT = {"source": "kn", "kn": {"youtube_url": "https://youtu.be/y"}}


def test_is_youtube_only():
    assert is_youtube_only(YT)
    assert not is_youtube_only(LOCAL)
    assert not is_youtube_only(DIVEBAR)
    assert not is_youtube_only(None)


def test_drop_youtube_only_filters_versions_and_empty_songs():
    songs = [
        {"key": "a", "version_count": 3, "versions": [LOCAL, YT, DIVEBAR]},
        {"key": "b", "version_count": 1, "versions": [YT]},
    ]
    out = drop_youtube_only(songs)
    assert [s["key"] for s in out] == ["a"]
    assert out[0]["versions"] == [LOCAL, DIVEBAR]
    assert out[0]["version_count"] == 2
    assert songs[0]["version_count"] == 3  # input untouched


def test_trip_and_close():
    b = YouTubeBreaker(FakeStore(), {})
    assert not b.is_open()
    b.trip("bot check", "https://youtu.be/x")
    assert b.is_open()
    st = b.status()
    assert st["reason"] == "bot check" and st["probe_url"] == "https://youtu.be/x"
    b.record_success()
    assert not b.is_open()


def test_state_survives_restart():
    store = FakeStore()
    YouTubeBreaker(store, {}).trip("broken", "https://youtu.be/x")
    assert YouTubeBreaker(store, {}).is_open()


def test_probe_is_due_after_interval():
    clock = Clock()
    b = YouTubeBreaker(FakeStore(), {"youtube_breaker_probe_interval_s": 1800}, clock=clock)
    assert not b.due_for_probe()  # closed
    b.trip("broken", "https://youtu.be/x")
    assert not b.due_for_probe()
    clock.now += 1799
    assert not b.due_for_probe()
    clock.now += 1
    assert b.due_for_probe()


def test_retrip_keeps_original_open_time():
    clock = Clock()
    b = YouTubeBreaker(FakeStore(), {}, clock=clock)
    b.trip("first", "https://youtu.be/a")
    clock.now += 600
    b.trip("second", "https://youtu.be/b")
    assert b.status()["opened_at"] == 1000.0
    assert b.status()["probe_url"] == "https://youtu.be/b"


def test_probe_success_closes():
    b = YouTubeBreaker(FakeStore(), {})
    b.trip("broken", "https://youtu.be/x")
    media = FakeMedia(("/v/x.mp4", "X"))
    assert b.probe(media) is True
    assert media.urls == ["https://youtu.be/x"]
    assert not b.is_open()


def test_probe_failure_stays_open_and_waits_again():
    clock = Clock()
    b = YouTubeBreaker(FakeStore(), {}, clock=clock)
    b.trip("broken", "https://youtu.be/x")
    clock.now += youtube_breaker.DEFAULT_PROBE_INTERVAL_S
    assert b.due_for_probe()
    assert b.probe(FakeMedia((None, None))) is False
    assert b.is_open()
    assert not b.due_for_probe()  # next probe is another interval away


def test_probe_video_gone_still_means_youtube_works():
    """yt-dlp reached YouTube and got a verdict about that one video → close."""
    b = YouTubeBreaker(FakeStore(), {})
    b.trip("broken", "https://youtu.be/x")
    assert b.probe(FakeMedia((None, None), "ERROR: [youtube] x: Private video")) is True
    assert not b.is_open()


def test_probe_bot_check_keeps_it_open():
    b = YouTubeBreaker(FakeStore(), {})
    b.trip("broken", "https://youtu.be/x")
    assert b.probe(FakeMedia((None, None), "Sign in to confirm you're not a bot")) is False
    assert b.is_open()
