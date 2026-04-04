"""Unit tests for rotation_data.py (conky display script)."""

import json
import os
import sys
import time

import pytest

# rotation_data.py lives in desktop/, add it to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "desktop"))

import rotation_data


@pytest.fixture
def cache_file(tmp_path):
    """Create a temporary cache file path."""
    return str(tmp_path / "rotation_cache.json")


@pytest.fixture
def sample_cache_data():
    """Sample cache data matching what rotation.py writes."""
    return {
        "queue": [
            {"singer": "Alice", "song_artist": "Bohemian Rhapsody - Queen", "status": "Now Singing"},
            {"singer": "Bob", "song_artist": "Don't Stop Believin - Journey", "status": "Up Next"},
            {"singer": "Carol", "song_artist": "Sweet Caroline - Neil Diamond", "status": "Waiting"},
        ],
        "stats": {
            "singers": 4,
            "sung": 1,
            "queued": 3,
            "started": "3/5 20:00",
        },
        "updated": time.time(),
    }


class TestReadLocalCache:
    def test_reads_valid_cache(self, cache_file, sample_cache_data, monkeypatch):
        monkeypatch.setattr(rotation_data, "CACHE_FILE", cache_file)
        with open(cache_file, "w") as f:
            json.dump(sample_cache_data, f)

        result = rotation_data.read_local_cache()
        assert result is not None
        queue, stats = result
        assert len(queue) == 3
        assert queue[0]["singer"] == "Alice"
        assert stats["singers"] == 4

    def test_returns_none_if_missing(self, cache_file, monkeypatch):
        monkeypatch.setattr(rotation_data, "CACHE_FILE", cache_file)
        assert rotation_data.read_local_cache() is None

    def test_returns_none_if_stale(self, cache_file, sample_cache_data, monkeypatch):
        monkeypatch.setattr(rotation_data, "CACHE_FILE", cache_file)
        with open(cache_file, "w") as f:
            json.dump(sample_cache_data, f)
        # Backdate the file modification time
        old_time = time.time() - rotation_data.CACHE_MAX_AGE - 10
        os.utime(cache_file, (old_time, old_time))

        assert rotation_data.read_local_cache() is None

    def test_returns_none_if_invalid_json(self, cache_file, monkeypatch):
        monkeypatch.setattr(rotation_data, "CACHE_FILE", cache_file)
        with open(cache_file, "w") as f:
            f.write("not json")

        assert rotation_data.read_local_cache() is None

    def test_returns_none_if_missing_keys(self, cache_file, monkeypatch):
        monkeypatch.setattr(rotation_data, "CACHE_FILE", cache_file)
        with open(cache_file, "w") as f:
            json.dump({"bad": "data"}, f)

        assert rotation_data.read_local_cache() is None

    def test_respects_max_entries(self, cache_file, monkeypatch):
        monkeypatch.setattr(rotation_data, "CACHE_FILE", cache_file)
        big_queue = [
            {"singer": f"Singer{i}", "song_artist": f"Song{i}", "status": "Waiting"}
            for i in range(20)
        ]
        data = {
            "queue": big_queue,
            "stats": {"singers": 20, "sung": 0, "queued": 20, "started": ""},
            "updated": time.time(),
        }
        with open(cache_file, "w") as f:
            json.dump(data, f)

        queue, _ = rotation_data.read_local_cache()
        assert len(queue) == rotation_data.MAX_ENTRIES


class TestFormatConky:
    def test_empty_queue(self, capsys):
        rotation_data.format_conky([])
        output = capsys.readouterr().out
        assert "No singers in queue" in output

    def test_now_singing_shows_exact_status(self, capsys):
        rotation_data.format_conky([
            {"singer": "Alice", "song_artist": "Test Song", "status": "Now Singing"},
        ])
        output = capsys.readouterr().out
        assert "Alice" in output
        assert "Now Singing" in output
        assert "Test Song" in output

    def test_up_next_shows_exact_status(self, capsys):
        rotation_data.format_conky([
            {"singer": "First", "song_artist": "", "status": "Now Singing"},
            {"singer": "Bob", "song_artist": "", "status": "Up Next"},
        ])
        output = capsys.readouterr().out
        assert "Up Next" in output

    def test_waiting_has_no_badge(self, capsys):
        rotation_data.format_conky([
            {"singer": "Carol", "song_artist": "", "status": "Waiting"},
        ])
        output = capsys.readouterr().out
        assert "Carol" in output
        # "Waiting" should not appear as a badge
        assert "Waiting" not in output

    def test_custom_status_shown_verbatim(self, capsys):
        rotation_data.format_conky([
            {"singer": "Del", "song_artist": "", "status": "Being Made (!)"},
        ])
        output = capsys.readouterr().out
        assert "Being Made (!)" in output

    def test_no_song_line_when_empty(self, capsys):
        rotation_data.format_conky([
            {"singer": "Alice", "song_artist": "", "status": "Now Singing"},
        ])
        output = capsys.readouterr().out
        lines = [l for l in output.strip().split("\n") if l.strip()]
        assert len(lines) == 1  # only singer line, no song line


class TestPaidIndicator:
    def test_paid_entry_shows_heart(self, capsys):
        rotation_data.format_conky([
            {"singer": "Alice", "song_artist": "Test Song", "status": "Waiting", "paid": True},
        ])
        output = capsys.readouterr().out
        assert "♥" in output
        assert "Alice" in output

    def test_unpaid_entry_no_heart(self, capsys):
        rotation_data.format_conky([
            {"singer": "Bob", "song_artist": "Test Song", "status": "Waiting", "paid": False},
        ])
        output = capsys.readouterr().out
        assert "♥" not in output

    def test_paid_missing_field_no_heart(self, capsys):
        """Backward compat: old cache data without paid field."""
        rotation_data.format_conky([
            {"singer": "Carol", "song_artist": "Test Song", "status": "Waiting"},
        ])
        output = capsys.readouterr().out
        assert "♥" not in output


class TestRulesMode:
    def test_rules_output_contains_header(self, capsys, tmp_path, monkeypatch):
        rules_file = str(tmp_path / "rules.txt")
        with open(rules_file, "w") as f:
            f.write("First come, first sing\nNew singers get priority\n")
        monkeypatch.setattr(rotation_data, "RULES_FILE", rules_file)
        rotation_data.format_rules()
        output = capsys.readouterr().out
        assert "HOW IT WORKS" in output

    def test_rules_output_contains_bullets(self, capsys, tmp_path, monkeypatch):
        rules_file = str(tmp_path / "rules.txt")
        with open(rules_file, "w") as f:
            f.write("First come, first sing\nNew singers get priority\n")
        monkeypatch.setattr(rotation_data, "RULES_FILE", rules_file)
        rotation_data.format_rules()
        output = capsys.readouterr().out
        assert "First come, first sing" in output
        assert "New singers get priority" in output

    def test_rules_missing_file_shows_nothing(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setattr(rotation_data, "RULES_FILE", str(tmp_path / "nonexistent.txt"))
        rotation_data.format_rules()
        output = capsys.readouterr().out
        assert output.strip() == ""

    def test_rules_positioned_on_right(self, capsys, tmp_path, monkeypatch):
        rules_file = str(tmp_path / "rules.txt")
        with open(rules_file, "w") as f:
            f.write("Test rule\n")
        monkeypatch.setattr(rotation_data, "RULES_FILE", rules_file)
        rotation_data.format_rules()
        output = capsys.readouterr().out
        assert "${goto 1020}" in output
