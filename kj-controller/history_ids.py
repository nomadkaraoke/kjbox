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
