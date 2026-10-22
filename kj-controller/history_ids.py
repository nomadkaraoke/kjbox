"""Stable ids for a singer's sung songs, shared by GET /sing/api/v1/me and the
history outbox to decide, so the app and decide name the same performance the
same way (the app de-duplicates its own log against it)."""

import hashlib


def history_id_for(box_id, entry_id, email):
    """``"kj_" + sha256(f"{box_id}:{entry_id}:{email}").hexdigest()[:24]`` with
    ``email`` stripped and lowercased (the gen account's email)."""
    email = (email or "").strip().lower()
    digest = hashlib.sha256(f"{box_id}:{entry_id}:{email}".encode()).hexdigest()
    return "kj_" + digest[:24]


DEFAULT_BOX_ID = "nomadpc"


def night_id_for_marker(box_id, night_started_at):
    """``sha256(f"{box_id}:{night_started_at}").hexdigest()[:16]``: the night's
    id for the app (v1 responses) and decide (history records)."""
    return hashlib.sha256(f"{box_id}:{night_started_at}".encode()).hexdigest()[:16]
