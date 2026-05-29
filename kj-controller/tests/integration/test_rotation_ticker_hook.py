"""Integration test — RotationManager._after_mutation fires rotation_ticker_sync.refresh."""

from unittest.mock import MagicMock


def test_mutation_invokes_rotation_ticker_sync(sing_app):
    sync = MagicMock()
    sing_app.rotation.rotation_ticker_sync = sync
    sing_app.rotation.add_entry("Alice", song_artist="Test Song")
    sync.refresh.assert_called()


def test_mutation_no_sync_no_op(sing_app):
    sing_app.rotation.rotation_ticker_sync = None
    # Should not raise
    sing_app.rotation.add_entry("Bob", song_artist="Another Song")


def test_sync_exception_does_not_block_mutation(sing_app):
    sync = MagicMock()
    sync.refresh.side_effect = RuntimeError("boom")
    sing_app.rotation.rotation_ticker_sync = sync
    # Must not raise even though refresh() blew up
    sing_app.rotation.add_entry("Carol", song_artist="Bohemian Rhapsody")
    entries = sing_app.rotation.get_rotation()
    assert any(e['singer'] == 'Carol' for e in entries)


def test_sync_wired_in_create_app(sing_app):
    """The factory must have attached a RotationTickerSync."""
    from rotation_ticker_sync import RotationTickerSync
    assert isinstance(sing_app.rotation.rotation_ticker_sync, RotationTickerSync)
