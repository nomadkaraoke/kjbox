"""Unit tests for the QR-above-ticker Z-order restack logic."""

import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'desktop'))


def _ticker_overlay(visible=True):
    from overlay_types import TickerOverlay
    ov = MagicMock(spec=TickerOverlay)
    ov.visible = visible
    return ov


def _qr_overlay(visible=True):
    from overlay_types import QRCodeOverlay
    ov = MagicMock(spec=QRCodeOverlay)
    ov.visible = visible
    return ov


class TestRestackQROverTicker:
    def test_no_qr_overlays_is_noop(self):
        from overlay_engine import OverlayEngine
        eng = OverlayEngine.__new__(OverlayEngine)
        eng.overlays = {'t': _ticker_overlay(visible=True)}
        eng._restack_qr_above_ticker()
        # Nothing to do; method must not blow up

    def test_qr_without_ticker_is_noop(self):
        from overlay_engine import OverlayEngine
        qr = _qr_overlay(visible=True)
        eng = OverlayEngine.__new__(OverlayEngine)
        eng.overlays = {'q': qr}
        eng._restack_qr_above_ticker()
        qr.destroy_window.assert_not_called()
        qr.create_window.assert_not_called()

    def test_qr_with_visible_ticker_restacks_qr_only(self):
        from overlay_engine import OverlayEngine
        t = _ticker_overlay(visible=True)
        q = _qr_overlay(visible=True)
        eng = OverlayEngine.__new__(OverlayEngine)
        eng.overlays = {'t': t, 'q': q}
        eng._restack_qr_above_ticker()
        q.destroy_window.assert_called_once()
        q.create_window.assert_called_once()
        q.render.assert_called_once()
        # Ticker is untouched
        t.destroy_window.assert_not_called()
        t.create_window.assert_not_called()

    def test_invisible_qr_is_skipped(self):
        from overlay_engine import OverlayEngine
        t = _ticker_overlay(visible=True)
        q = _qr_overlay(visible=False)
        eng = OverlayEngine.__new__(OverlayEngine)
        eng.overlays = {'t': t, 'q': q}
        eng._restack_qr_above_ticker()
        q.destroy_window.assert_not_called()
