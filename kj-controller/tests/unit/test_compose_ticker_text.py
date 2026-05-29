"""Unit tests for compose_ticker_text — the pure ticker composition function."""

from rotation_ticker_sync import compose_ticker_text


def _entries(*names):
    return [{"singer": n, "song_artist": "", "status": "Waiting"} for n in names]


class TestComposeTickerText:
    def test_empty_rotation_returns_prefix_plus_empty_text(self):
        out = compose_ticker_text(
            entries=[],
            prefix="Up next: ",
            count=5,
            separator="   ",
            empty_text="Sign up at the booth!",
        )
        assert out == "Up next: Sign up at the booth!"

    def test_single_singer_numbered(self):
        out = compose_ticker_text(
            entries=_entries("Alice"),
            prefix="Up next: ",
            count=5,
            separator="   ",
            empty_text="",
        )
        assert out == "Up next: 1. Alice"

    def test_five_singers_numbered_in_order(self):
        out = compose_ticker_text(
            entries=_entries("Alice", "Bob", "Carol", "Dave", "Eve"),
            prefix="Up next: ",
            count=5,
            separator="   ",
            empty_text="",
        )
        assert out == "Up next: 1. Alice   2. Bob   3. Carol   4. Dave   5. Eve"

    def test_count_truncates_overflow(self):
        out = compose_ticker_text(
            entries=_entries("Alice", "Bob", "Carol", "Dave", "Eve", "Frank"),
            prefix="Up next: ",
            count=3,
            separator=" | ",
            empty_text="",
        )
        assert out == "Up next: 1. Alice | 2. Bob | 3. Carol"

    def test_fewer_singers_than_count(self):
        out = compose_ticker_text(
            entries=_entries("Alice", "Bob"),
            prefix="Up next: ",
            count=5,
            separator="   ",
            empty_text="",
        )
        assert out == "Up next: 1. Alice   2. Bob"

    def test_custom_prefix_and_separator(self):
        out = compose_ticker_text(
            entries=_entries("Alice", "Bob"),
            prefix="Rotation >>> ",
            count=5,
            separator=" • ",
            empty_text="",
        )
        assert out == "Rotation >>> 1. Alice • 2. Bob"

    def test_empty_prefix(self):
        out = compose_ticker_text(
            entries=_entries("Alice"),
            prefix="",
            count=5,
            separator="   ",
            empty_text="",
        )
        assert out == "1. Alice"

    def test_unicode_singer_names_pass_through(self):
        out = compose_ticker_text(
            entries=_entries("Renée", "山田太郎", "🎤 Karaoke King"),
            prefix="Up next: ",
            count=5,
            separator=" • ",
            empty_text="",
        )
        assert out == "Up next: 1. Renée • 2. 山田太郎 • 3. 🎤 Karaoke King"

    def test_count_zero_returns_empty_message(self):
        out = compose_ticker_text(
            entries=_entries("Alice", "Bob"),
            prefix="Up next: ",
            count=0,
            separator="   ",
            empty_text="Empty",
        )
        # Count=0 means "no slots configured" — treat as empty rotation for the user.
        assert out == "Up next: Empty"
