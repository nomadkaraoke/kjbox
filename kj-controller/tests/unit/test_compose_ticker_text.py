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


from unittest.mock import MagicMock

from rotation_ticker_sync import RotationTickerSync


def _ticker(oid, source='rotation', text='', prefix='Up next: ', count=5,
            separator='   ', empty_text='Sign up at the booth!'):
    return {
        'id': oid,
        'type': 'ticker',
        'name': '',
        'enabled': True,
        'show_over_video': True,
        'config': {
            'source': source,
            'text': text,
            'prefix': prefix,
            'count': count,
            'separator': separator,
            'empty_text': empty_text,
        },
    }


class TestRotationTickerSync:
    def test_refresh_no_rotation_tickers_is_noop(self):
        om = MagicMock()
        om.list_overlays.return_value = [_ticker('a', source='static', text='static text')]
        store = MagicMock()
        store.get_entries.return_value = _entries('Alice')
        sync = RotationTickerSync(om, store)

        n = sync.refresh()
        assert n == 0
        om.update_overlay.assert_not_called()

    def test_refresh_updates_rotation_ticker_text(self):
        ticker = _ticker('t1')
        om = MagicMock()
        om.list_overlays.return_value = [ticker]
        store = MagicMock()
        store.get_entries.return_value = _entries('Alice', 'Bob')
        sync = RotationTickerSync(om, store)

        n = sync.refresh()
        assert n == 1
        om.update_overlay.assert_called_once()
        oid, updates = om.update_overlay.call_args.args
        assert oid == 't1'
        assert updates['config']['text'] == 'Up next: 1. Alice   2. Bob'

    def test_refresh_idempotent_when_text_unchanged(self):
        existing_text = 'Up next: 1. Alice'
        ticker = _ticker('t1', text=existing_text)
        om = MagicMock()
        om.list_overlays.return_value = [ticker]
        store = MagicMock()
        store.get_entries.return_value = _entries('Alice')
        sync = RotationTickerSync(om, store)

        n = sync.refresh()
        assert n == 0
        om.update_overlay.assert_not_called()

    def test_refresh_updates_multiple_rotation_tickers(self):
        a = _ticker('a', prefix='A: ', count=1)
        b = _ticker('b', prefix='B: ', count=2)
        om = MagicMock()
        om.list_overlays.return_value = [a, b]
        store = MagicMock()
        store.get_entries.return_value = _entries('Alice', 'Bob', 'Carol')
        sync = RotationTickerSync(om, store)

        n = sync.refresh()
        assert n == 2
        calls = {call.args[0]: call.args[1] for call in om.update_overlay.call_args_list}
        assert calls['a']['config']['text'] == 'A: 1. Alice'
        assert calls['b']['config']['text'] == 'B: 1. Alice   2. Bob'

    def test_refresh_preserves_other_config_fields(self):
        ticker = _ticker('t1')
        ticker['config']['speed'] = 3
        ticker['config']['bg_color'] = '#abcdef'
        om = MagicMock()
        om.list_overlays.return_value = [ticker]
        store = MagicMock()
        store.get_entries.return_value = _entries('Alice')
        sync = RotationTickerSync(om, store)

        sync.refresh()
        updates = om.update_overlay.call_args.args[1]
        assert updates['config']['speed'] == 3
        assert updates['config']['bg_color'] == '#abcdef'

    def test_refresh_swallows_exceptions(self):
        om = MagicMock()
        om.list_overlays.side_effect = RuntimeError('boom')
        store = MagicMock()
        sync = RotationTickerSync(om, store)

        # Must not raise
        n = sync.refresh()
        assert n == 0
