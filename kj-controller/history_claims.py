"""History claims: a singer claims the songs they sang at past Nomad nights
(Phase 4c, spec 6.10).

Matching is deliberately loose (Andrew: "people often use variations of their
name, sometimes with or without a last initial") but never automatic: a box
name matches an account when its folded FIRST word equals the first word of
any name the account goes by (R35). The songs are shown before anything is
claimed, and only an explicit claim attributes an entry.

- ``first_word`` folds with ``singer_names.fold_name`` and needs at least
  ``MIN_FIRST_WORD`` characters, so "W" or "J. Smith" never match anyone.
- ``entry_matches`` checks each member of an entry (a duet matches on any
  listed singer).
- ``account_names`` collects the names an account goes by: the gen profile
  display name (no email-derived fallback), the names its devices used on
  their own requests on any night, and those devices' aliases (KJ renames and
  merges included).
"""

from singer_names import fold_name

MIN_FIRST_WORD = 2


def first_word(name):
    """Folded first word of ``name``, or None when it is shorter than
    ``MIN_FIRST_WORD`` (or there is no word at all)."""
    words = fold_name(name).split()
    if not words or len(words[0]) < MIN_FIRST_WORD:
        return None
    return words[0]


def entry_matches(entry_members, account_words):
    """The members of an entry whose first word is one of ``account_words``,
    in the entry's order."""
    if not account_words:
        return []
    return [m for m in (entry_members or []) if first_word(m) in account_words]


def account_names(store, email, display_name, app_device):
    """``{"words": set[str], "names": list[str]}``: the names the gen account
    ``email`` goes by on this box and their matchable first words.

    Sources (R35), in order: the gen ``display_name``; the singer names on the
    non-friend, non-meta requests of the account's devices across all nights;
    those devices' aliases. The devices are ``app_device`` plus every device
    ``singer_accounts`` ever linked to the email (app and claimed web devices).
    """
    devices = [d for d in [app_device] if d]
    for d in store.devices_for_email_any_night(email):
        if d not in devices:
            devices.append(d)

    candidates = [display_name]
    candidates.extend(store.request_names_any_night(devices))
    for d in devices:
        candidates.append(store.get_alias(d))

    names, seen, words = [], set(), set()
    for raw in candidates:
        name = (raw or "").strip()
        folded = fold_name(name)
        if not folded or folded in seen:
            continue
        seen.add(folded)
        names.append(name)
        w = first_word(name)
        if w:
            words.add(w)
    return {"words": words, "names": names}
