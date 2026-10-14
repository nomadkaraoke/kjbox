import datetime as dt
import json
import subprocess

import pytest

from nightclips import kjbox


class FakeSSH:
    """Stands in for subprocess.run(['ssh', host, 'curl ... URL'])."""

    def __init__(self, routes, rc=0):
        self.routes, self.rc, self.calls = routes, rc, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        assert cmd[0] == "ssh"
        remote = cmd[-1]
        if self.rc:
            return subprocess.CompletedProcess(cmd, self.rc, "", "ssh: connect to host nomadpctunnel: timed out")
        for frag, (status, body) in self.routes.items():
            if frag in remote:
                return subprocess.CompletedProcess(cmd, 0, f"{body}\n{status}", "")
        return subprocess.CompletedProcess(cmd, 0, "not found\n404", "")


def test_night_media_used_when_available(night_media_payload):
    run = FakeSSH({"/history/night-media?night=2026-10-08": (200, json.dumps(night_media_payload))})
    d = kjbox.fetch_night("2026-10-08", runner=run)
    assert d["warning"] is None
    assert d["consent_source"] == "legacy"
    assert len(d["songs"]) == 71
    assert len(run.calls) == 1
    assert any(s["postable"] for s in d["songs"])


def test_falls_back_to_night_log_on_404(night_log):
    run = FakeSSH({
        "/history/night-media": (404, "<!doctype html><title>404 Not Found</title>"),
        "/history/night?night=2026-10-08": (200, json.dumps(night_log)),
    })
    d = kjbox.fetch_night("2026-10-08", runner=run)
    assert d["consent_source"] == "unavailable"
    assert d["warning"] and "UNKNOWN" in d["warning"]
    assert len(d["songs"]) == 71  # 82 entries, 11 never sung
    assert all(not s["postable"] for s in d["songs"])
    assert all(p["consent"] == "unknown" for s in d["songs"] for p in s["performers"])
    # composite name without a singers list stays one performer (server resolves parts)
    ana = next(s for s in d["songs"] if s["singer"] == "Ana & Anya")
    assert [p["name"] for p in ana["performers"]] == ["Ana & Anya"]


def test_unreachable_raises():
    with pytest.raises(kjbox.KjboxUnreachable, match="timed out"):
        kjbox.fetch_night("2026-10-08", runner=FakeSSH({}, rc=255))


def test_server_error_raises():
    run = FakeSSH({"/history/night-media": (500, "boom")})
    with pytest.raises(kjbox.KjboxUnreachable, match="HTTP 500"):
        kjbox.fetch_night("2026-10-08", runner=run)


def test_bad_night_rejected():
    with pytest.raises(ValueError):
        kjbox.fetch_night("10/08/2026", runner=FakeSSH({}))


def test_split_performers_prefers_duet_list():
    assert kjbox.split_performers({"singer": "A & B", "singers": ["A", "B"]}) == ["A", "B"]
    assert kjbox.split_performers({"singer": "A & B", "singers": None}) == ["A & B"]
    assert kjbox.split_performers({"singer": "", "singers": None}) == []


def test_normalize_never_trusts_inconsistent_postable():
    data = {"songs": [{"sung_at": "2026-10-08 21:00:00", "song": "x", "postable": True,
                       "performers": [{"name": "A", "consent": "yes"}, {"name": "B", "consent": "unknown"}]}]}
    assert kjbox.normalize_night_media(data)[0]["postable"] is False


@pytest.mark.parametrize(
    "now,expected",
    [
        (dt.datetime(2026, 10, 9, 10, 0), "2026-10-08"),   # Friday morning -> yesterday
        (dt.datetime(2026, 10, 9, 3, 59), "2026-10-01"),   # show still running
        (dt.datetime(2026, 10, 9, 4, 0), "2026-10-08"),
        (dt.datetime(2026, 10, 8, 22, 0), "2026-10-01"),   # Thursday night, show on
        (dt.datetime(2026, 10, 14, 15, 0), "2026-10-08"),  # Wednesday
        (dt.datetime(2026, 10, 11, 12, 0), "2026-10-08"),  # Sunday
    ],
)
def test_default_night(now, expected):
    assert kjbox.default_night(now) == expected
