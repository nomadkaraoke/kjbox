"""history_claims: loose name matching and the names an account goes by on the
box (Phase 4c, spec 6.10, R35). Fixture singers are invented names."""

import pytest

from history_claims import MIN_FIRST_WORD, account_names, entry_matches, first_word
from sing_store import SingStore

OLD_NIGHT = "2026-09-03 19:00:00"
TONIGHT = "2026-10-29 19:00:00"
EMAIL = "e2e-claims@example.com"


@pytest.fixture
def store(tmp_path):
    s = SingStore(str(tmp_path / "rotation.db"))
    yield s
    s.close()


def _req(store, device, name, created_at=OLD_NIGHT, source_type="local", status="approved"):
    req = store.create_request(name, "", song_artist="Muse", song_title="Starlight",
                               source_type=source_type, device_id=device)
    conn = store._get_conn()
    conn.execute("UPDATE sing_requests SET created_at=?, status=? WHERE id=?",
                 (created_at, status, req["id"]))
    conn.commit()
    return req


# --- first_word ---------------------------------------------------------------

def test_first_word_folds_accents_case_punctuation():
    assert first_word("Wrén B.") == "wren"
    assert first_word("WREN") == "wren"
    assert first_word(" wren  b ") == "wren"


def test_first_word_min_length():
    assert MIN_FIRST_WORD == 2
    assert first_word("W") is None
    assert first_word("J. Smith") is None  # first word "j" is too short
    assert first_word("") is None
    assert first_word(None) is None
    assert first_word("...") is None


# --- entry_matches ------------------------------------------------------------

def test_entry_matches_ignores_rest_of_name():
    words = {"wren"}
    assert entry_matches(["Wren B"], words) == ["Wren B"]
    assert entry_matches(["Wren Bloggs"], words) == ["Wren Bloggs"]
    assert entry_matches(["wren b."], words) == ["wren b."]
    assert entry_matches(["Wrenna"], words) == []
    assert entry_matches(["DJ Wren"], words) == []
    assert entry_matches(["W"], words) == []


def test_duet_matches_any_member():
    assert entry_matches(["Juno", "Wren B"], {"wren"}) == ["Wren B"]
    assert entry_matches(["Juno", "Wren B"], {"wren", "juno"}) == ["Juno", "Wren B"]
    assert entry_matches(["Juno", "Wren B"], set()) == []


# --- account_names ------------------------------------------------------------

def test_account_names_sources(store):
    app_dev = "app-device-1"
    web_dev = "web-device-1"
    other_dev = "web-device-other"
    # A claimed web device (any night, not tonight).
    store.link_account(web_dev, EMAIL, "claimed", OLD_NIGHT)
    # An app-device request on an old night.
    _req(store, app_dev, "Quill")
    # A claimed web device's request.
    _req(store, web_dev, "Juno K")
    # A KJ merge alias on the web device.
    store.set_alias(web_dev, "Sparrow", origin="kj")
    # A friend request from the app device does NOT count.
    friend = _req(store, app_dev, "Moss")
    store.mark_friend_request(friend["id"], "Moss")
    # Reorder / tip meta-requests, and dead requests, don't count.
    _req(store, app_dev, "Tipper", source_type="tip")
    _req(store, app_dev, "Reorderer", source_type="reorder")
    _req(store, app_dev, "Cancelled", status="cancelled")
    # Another person's device is not this account's.
    _req(store, other_dev, "Juniper")

    got = account_names(store, EMAIL, "Wren B", app_dev)
    assert got["words"] == {"wren", "quill", "juno", "sparrow"}
    assert set(got["names"]) == {"Wren B", "Quill", "Juno K", "Sparrow"}
    assert got["names"][0] == "Wren B"  # the profile name first


def test_account_names_email_matched_case_insensitively(store):
    store.link_account("web-dev", EMAIL, "claimed", OLD_NIGHT)
    _req(store, "web-dev", "Quill")
    got = account_names(store, EMAIL.upper(), None, "app-dev")
    assert got["words"] == {"quill"}


def test_account_names_device_moved_to_another_account(store):
    # dev-1 was A's phone on night 1, then B signed in on it on night 2.
    a, b = "e2e-a@example.com", "e2e-b@example.com"
    night1, night2 = "2026-09-03 19:00:00", "2026-09-10 19:00:00"
    store.link_account("dev-1", a, "claimed", night1)
    _req(store, "dev-1", "Quill", created_at="2026-09-03 21:00:00")
    store.link_account("dev-1", b, "claimed", night2)
    _req(store, "dev-1", "Juniper", created_at="2026-09-10 21:00:00")
    store.set_alias("dev-1", "Juniper B", origin="kj")
    conn = store._get_conn()
    conn.execute("UPDATE singer_accounts SET linked_at=night_started_at")
    conn.commit()

    got_a = account_names(store, a, None, "app-dev-a")
    assert got_a["words"] == {"quill"}  # not B's request, not B's alias
    got_b = account_names(store, b, None, "app-dev-b")
    assert got_b["words"] == {"juniper"}
    assert set(got_b["names"]) == {"Juniper", "Juniper B"}


def test_account_names_web_device_counts_only_linked_nights(store):
    store.link_account("dev-w", EMAIL, "claimed", "2026-09-03 19:00:00")
    _req(store, "dev-w", "Quill", created_at="2026-09-03 21:00:00")
    _req(store, "dev-w", "Moss", created_at="2026-09-17 21:00:00")  # not linked that night
    _req(store, "dev-w", "Earlier", created_at="2026-08-27 21:00:00")
    assert account_names(store, EMAIL, None, "app-dev")["words"] == {"quill"}


def test_account_names_app_device_needs_no_link(store):
    # The app device is the HMAC of the verified email: every request is theirs.
    _req(store, "app-dev", "Quill", created_at="2026-05-01 21:00:00")
    store.set_alias("app-dev", "Sparrow")
    assert account_names(store, EMAIL, None, "app-dev")["words"] == {"quill", "sparrow"}


def test_account_names_no_email_fallback(store):
    got = account_names(store, "wren.bloggs@example.com", None, "app-device-1")
    assert got == {"words": set(), "names": []}
    got = account_names(store, "wren.bloggs@example.com", "", "app-device-1")
    assert got == {"words": set(), "names": []}


def test_account_names_skips_too_short_first_words(store):
    got = account_names(store, EMAIL, "J. Wren", "app-device-1")
    assert got["words"] == set()


# --- SingStore helpers --------------------------------------------------------

def test_devices_for_email_any_night(store):
    store.link_account("dev-a", EMAIL, "app", OLD_NIGHT)
    store.link_account("dev-b", EMAIL, "claimed", TONIGHT)
    store.link_account("dev-a", EMAIL, "app", TONIGHT)
    store.link_account("dev-c", "someone-else@example.com", "app", TONIGHT)
    assert sorted(store.devices_for_email_any_night(EMAIL)) == ["dev-a", "dev-b"]
    assert store.devices_for_email_any_night("") == []


def test_request_names_any_night_ignores_since(store):
    _req(store, "dev-a", "Quill", created_at="2026-06-01 20:00:00")
    _req(store, "dev-a", "Quill", created_at=TONIGHT)
    assert store.request_names_any_night(["dev-a"]) == ["Quill"]
    assert store.request_names_any_night([]) == []
