"""Tests for app-level callback factories wired into the coordinator."""

from unittest.mock import MagicMock

from app import _make_on_karaoke_end


def test_on_karaoke_end_clears_overlay_and_fades_in_filler():
    """Natural EOF must hide the overlay and resume filler music — the
    April 17 renderer refactor moved fade_in out of the player's EOF
    handler into this callback, so both calls must be wired here."""
    overlay_mgr = MagicMock()
    coordinator = MagicMock()

    cb = _make_on_karaoke_end(overlay_mgr, coordinator)
    cb()

    overlay_mgr.set_karaoke_playing.assert_called_once_with(False)
    coordinator.fade_in_filler.assert_called_once()


def test_on_karaoke_end_still_fades_filler_when_overlay_fails():
    """Filler fade-in must not be skipped if the overlay call raises —
    an overlay glitch shouldn't leave the show in silence."""
    overlay_mgr = MagicMock()
    overlay_mgr.set_karaoke_playing.side_effect = RuntimeError('boom')
    coordinator = MagicMock()

    cb = _make_on_karaoke_end(overlay_mgr, coordinator)
    cb()

    coordinator.fade_in_filler.assert_called_once()


def test_on_karaoke_end_still_clears_overlay_when_filler_fails():
    overlay_mgr = MagicMock()
    coordinator = MagicMock()
    coordinator.fade_in_filler.side_effect = RuntimeError('boom')

    cb = _make_on_karaoke_end(overlay_mgr, coordinator)
    cb()

    overlay_mgr.set_karaoke_playing.assert_called_once_with(False)
