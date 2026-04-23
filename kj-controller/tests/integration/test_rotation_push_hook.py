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
