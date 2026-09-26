"""Integration test — RotationManager._after_mutation fires push dispatch."""

from unittest.mock import MagicMock


def test_mutation_invokes_push_dispatcher(sing_app):
    dispatcher = MagicMock()
    sing_app.rotation.push_dispatcher = dispatcher
    sing_app.rotation.add_entry("Alice", song_artist="Test Song")
    dispatcher.notify_rotation_changed.assert_called()


def test_mutation_no_push_dispatcher_no_op(sing_app):
    sing_app.rotation.push_dispatcher = None
    # Should not raise
    sing_app.rotation.add_entry("Bob", song_artist="Another Song")


def test_dispatcher_exception_does_not_block_mutation(sing_app):
    """A push dispatcher bug must never prevent the rotation mutation from
    completing — push is non-critical."""
    dispatcher = MagicMock()
    dispatcher.notify_rotation_changed.side_effect = RuntimeError("boom")
    sing_app.rotation.push_dispatcher = dispatcher
    # Should not raise despite the dispatcher throwing
    entry = sing_app.rotation.add_entry("Charlie", song_artist="Some Song")
    assert entry["id"] > 0


def test_dispatcher_wired_in_create_app(sing_app):
    """After create_app runs, rotation.push_dispatcher should be a real
    PushDispatcher (not None)."""
    from push_dispatcher import PushDispatcher
    assert isinstance(sing_app.rotation.push_dispatcher, PushDispatcher)


def test_phone_lookup_resolves_via_sing_requests(sing_app):
    """The get_linked_phone_for_entry callback should return the phone
    associated with a rotation entry via the sing_request that linked it."""
    # Create a sing_request + rotation entry + link
    req = sing_app.sing_store.create_request(
        singer_name="Alice", phone="+61400000001",
        song_artist="Queen", song_title="Bohemian Rhapsody",
        source_type="make", source_ref=None, source_meta=None, notes="",
    )
    entry = sing_app.rotation.add_entry("Alice", song_artist="Queen — Bohemian Rhapsody")
    sing_app.sing_store.mark_approved(req["id"], linked_entry_id=entry["id"])

    dispatcher = sing_app.rotation.push_dispatcher
    phone = dispatcher.get_linked_phone_for_entry(entry)
    assert phone == "+61400000001"

    # Un-linked rotation entry returns None
    unlinked = sing_app.rotation.add_entry("Bob", song_artist="Manual entry")
    assert dispatcher.get_linked_phone_for_entry(unlinked) is None


def test_phoneless_singer_gets_ladder_push_by_device(sing_app):
    """2026-09-24: "Jasssss" signed up without a phone, enabled push, and never
    got a single notification. Real wiring end to end: subscribe by device →
    the singer's approved entry resolves to that sub → a ladder push is sent."""
    from unittest.mock import patch

    store = sing_app.sing_store
    token = store.ensure_token()
    req = store.create_request(
        singer_name="Jasssss", phone="", device_id="dev-jas",
        song_artist="Queen", song_title="Bohemian Rhapsody",
        source_type="make", source_ref=None, source_meta=None, notes="",
    )
    # Another phone-less singer ahead of her must NOT be treated as hers.
    other = store.create_request(
        singer_name="Kim", phone="", device_id="dev-kim",
        song_artist="ABBA", song_title="Waterloo",
        source_type="make", source_ref=None, source_meta=None, notes="",
    )
    e_other = sing_app.rotation.add_entry("Kim", song_artist="ABBA — Waterloo")
    store.mark_approved(other["id"], linked_entry_id=e_other["id"])
    entry = sing_app.rotation.add_entry("Jasssss", song_artist="Queen — Bohemian Rhapsody")
    store.mark_approved(req["id"], linked_entry_id=entry["id"])
    store.insert_push_subscription(
        token=token, phone="", singer_name="Jasssss", endpoint="https://push/jas",
        p256dh="p", auth="a", device_id="dev-jas",
    )

    dispatcher = sing_app.rotation.push_dispatcher
    assert dispatcher.get_linked_device_for_entry(entry) == "dev-jas"
    with patch("push_dispatcher.webpush") as wp:
        dispatcher._dispatch_now()
        dispatcher.executor.shutdown(wait=True)
    assert wp.call_count == 1
    assert wp.call_args.kwargs["subscription_info"]["endpoint"] == "https://push/jas"
    (sub,) = store.list_active_push_subscriptions(token)
    assert '"entry_id": %d' % entry["id"] in sub["last_sent_state"]
