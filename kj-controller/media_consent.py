"""Per-song social-media consent resolution.

A performer's consent for ONE song resolves as:

1. explicit per-song overrides for that person — the requester's own override
   (``sing_requests.media_consent``, only for the singer who made the request)
   and KJ overrides for their name or for the whole song (``'*'``). Any 'no'
   wins; otherwise any 'yes';
2. else their night default (the photo-consent toggle);
3. else 'unknown' — which callers must treat as NO.

A song is postable only when every performer resolves to 'yes'.
"""

import re

_SPLIT_RE = re.compile(r"\s*(?:&|,|\band\b|\+)\s*", re.I)


def fold(name):
    return " ".join((name or "").split()).casefold()


def split_performers(entry):
    """Performer names for a rotation/night-log entry: the duet list when
    present, else the single ``singer`` string."""
    singers = entry.get("singers")
    if isinstance(singers, list):
        names = [n for n in singers if isinstance(n, str) and n.strip()]
        if names:
            return names
    name = entry.get("singer") or ""
    return [name] if name.strip() else []


def _name_parts(name):
    return [p for p in _SPLIT_RE.split(name or "") if p.strip()]


def _resolve_one(name, request_row, kj_overrides, night_defaults):
    key = fold(name)
    overrides = []
    if request_row and request_row.get("media_consent") in ("yes", "no") \
            and fold(request_row.get("singer_name")) == key:
        overrides.append((request_row["media_consent"], "song_override_singer"))
    for k in (key, "*"):
        c = (kj_overrides or {}).get(k)
        if c in ("yes", "no"):
            overrides.append((c, "song_override_kj"))
    if overrides:
        for want in ("no", "yes"):
            hits = [r for c, r in overrides if c == want]
            if hits:
                reason = "song_override_kj" if "song_override_kj" in hits else hits[0]
                return want, reason
    rec = (night_defaults or {}).get(key)
    if rec and rec.get("consent") in ("yes", "no"):
        return rec["consent"], "night_default"
    return "unknown", "unknown"


def resolve_performer(name, request_row=None, kj_overrides=None, night_defaults=None):
    """``{"name", "consent", "reason", "instagram"}`` for one performer.

    A composite label ("Ana & Brooke") with no answer of its own is resolved
    part by part: yes only if every part is yes."""
    consent, reason = _resolve_one(name, request_row, kj_overrides, night_defaults)
    handle = ((night_defaults or {}).get(fold(name)) or {}).get("instagram")
    if consent == "unknown":
        parts = _name_parts(name)
        if len(parts) > 1:
            results = [_resolve_one(p, request_row, kj_overrides, night_defaults) for p in parts]
            consents = {c for c, _ in results}
            if "no" in consents:
                consent, reason = "no", next(r for c, r in results if c == "no")
            elif consents == {"yes"}:
                consent, reason = "yes", results[0][1]
    return {"name": name, "consent": consent, "reason": reason, "instagram": handle}


def resolve_song(entry, request_row=None, kj_overrides=None, night_defaults=None):
    """``(performers, postable)`` for one sung entry."""
    performers = [resolve_performer(n, request_row, kj_overrides, night_defaults)
                  for n in split_performers(entry)]
    postable = bool(performers) and all(p["consent"] == "yes" for p in performers)
    return performers, postable
