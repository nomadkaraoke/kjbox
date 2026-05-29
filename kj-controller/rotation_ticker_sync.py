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
