import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import rotation_source as rs


def _write_cache(tmp_path, queue, stats, updated=None):
    p = tmp_path / "rotation_cache.json"
    p.write_text(json.dumps({
        "queue": queue, "stats": stats,
        "updated": updated if updated is not None else time.time(),
    }))
    return str(p)


def test_load_returns_queue_and_stats(tmp_path):
    path = _write_cache(tmp_path,
        [{"singer": "Alice", "song_artist": "Song - Artist", "status": "Now Singing", "paid": True}],
        {"started": "2026-06-04 20:54:55", "singers": 24, "sung": 40, "queued": 13})
    snap = rs.load_snapshot(path)
    assert snap.online is True
    assert snap.stats["singers"] == 24
    assert snap.queue[0].singer == "Alice"
    assert snap.queue[0].paid is True


def test_stale_cache_is_offline(tmp_path):
    path = _write_cache(tmp_path, [], {}, updated=time.time() - 9999)
    # force mtime into the past
    os.utime(path, (time.time() - 9999, time.time() - 9999))
    snap = rs.load_snapshot(path, max_age=120)
    assert snap.online is False


def test_missing_cache_is_offline(tmp_path):
    snap = rs.load_snapshot(str(tmp_path / "nope.json"))
    assert snap.online is False
    assert snap.queue == []


def test_status_color_mapping():
    assert rs.status_color("Now Singing") == "#2d8a4e"
    assert rs.status_color("Up Next") == "#d4720a"
    assert rs.status_color("waiting") == "#d4720a"
    assert rs.status_color("Being Made") == "#cc3333"
    assert rs.status_color("On Hold") == "#888888"
    assert rs.status_color("BRB") == "#888888"
    assert rs.status_color("Skipped") == "#3b82f6"
    assert rs.status_color("anything else") == "#8892a4"


def test_badge_text_hidden_for_waiting_and_empty():
    assert rs.badge_text("waiting") is None
    assert rs.badge_text("") is None
    assert rs.badge_text("Now Singing") == "Now Singing"


def test_paginate_single_page():
    q = list(range(5))
    page, start, page_num, total = rs.paginate(q, now=0.0)
    assert (page, start, page_num, total) == ([0, 1, 2, 3, 4], 0, 0, 1)


def test_paginate_cycles_every_10s():
    q = list(range(25))  # 3 pages of 10/10/5
    p0 = rs.paginate(q, now=0.0)
    p1 = rs.paginate(q, now=10.0)
    p2 = rs.paginate(q, now=20.0)
    p_wrap = rs.paginate(q, now=30.0)
    assert p0[2] == 0 and p0[1] == 0 and p0[0] == list(range(0, 10))
    assert p1[2] == 1 and p1[1] == 10 and p1[0] == list(range(10, 20))
    assert p2[2] == 2 and p2[1] == 20 and p2[0] == list(range(20, 25))
    assert p_wrap[2] == 0  # wraps back to page 0
    assert p0[3] == 3      # total_pages


def test_compose_ticker_text_basic():
    entries = [type("E", (), {"singer": "Alice"})(), type("E", (), {"singer": "Bob"})()]
    out = rs.compose_ticker_text(entries, prefix="Up next: ", count=5,
                                 separator="   ", empty_text="none")
    assert out == "Up next: 1. Alice   2. Bob"


def test_compose_ticker_text_empty():
    assert rs.compose_ticker_text([], "Up next: ", 5, "   ", "Scan the QR!") == "Up next: Scan the QR!"
    assert rs.compose_ticker_text([1, 2], "P:", 0, " ", "x") == "P:x"
