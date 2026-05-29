"""Rotation ticker sync — composes ticker text from the rotation snapshot
and updates ticker overlays whose source is 'rotation'.

The engine stays a dumb renderer of config.text; this module is the only
place that knows how to derive that text from the rotation queue.
"""

import logging

logger = logging.getLogger(__name__)


def compose_ticker_text(entries, prefix, count, separator, empty_text):
    """Return the ticker text string for a rotation snapshot.

    Args:
        entries: list of rotation entries (each a dict with at least "singer").
            Caller is responsible for filtering done/left and ordering.
        prefix: text prepended once before the dynamic list.
        count: max singers to include. count<=0 is treated as empty.
        separator: string inserted between numbered slots.
        empty_text: shown after prefix when no singers fit.

    Returns:
        The composed string, e.g. "Up next: 1. Alice   2. Bob".
    """
    if count <= 0:
        return f"{prefix}{empty_text}"

    slice_ = entries[:count]
    if not slice_:
        return f"{prefix}{empty_text}"

    slots = [f"{i}. {e['singer']}" for i, e in enumerate(slice_, start=1)]
    return f"{prefix}{separator.join(slots)}"


class RotationTickerSync:
    """Updates ticker overlays whose source is 'rotation' from the rotation queue.

    Hooked into RotationManager._after_mutation(). Best-effort: never raises.
    """

    def __init__(self, overlay_manager, rotation_store):
        self.overlay_manager = overlay_manager
        self.rotation_store = rotation_store

    def refresh(self):
        """Recompose text for every rotation ticker. Returns count updated."""
        try:
            overlays = self.overlay_manager.list_overlays()
        except Exception:
            logger.exception("rotation_ticker_sync: list_overlays failed")
            return 0

        try:
            entries = self.rotation_store.get_entries()
        except Exception:
            logger.exception("rotation_ticker_sync: get_entries failed")
            return 0

        updated = 0
        for overlay in overlays:
            if overlay.get('type') != 'ticker':
                continue
            cfg = overlay.get('config') or {}
            if cfg.get('source') != 'rotation':
                continue

            new_text = compose_ticker_text(
                entries=entries,
                prefix=cfg.get('prefix', 'Up next: '),
                count=int(cfg.get('count', 5) or 0),
                separator=cfg.get('separator', '   '),
                empty_text=cfg.get('empty_text', ''),
            )
            if cfg.get('text') == new_text:
                continue  # No-op: avoid spurious file write

            new_cfg = dict(cfg)
            new_cfg['text'] = new_text
            try:
                self.overlay_manager.update_overlay(overlay['id'], {'config': new_cfg})
                updated += 1
            except Exception:
                logger.exception("rotation_ticker_sync: update_overlay failed")
        return updated
