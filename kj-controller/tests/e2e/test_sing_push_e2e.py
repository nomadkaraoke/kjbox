"""End-to-end: subscribe + rotation mutations fire the expected push ladder.

Verifies:
- The full dispatcher path runs end-to-end (RotationManager mutation →
  _after_mutation → PushDispatcher → webpush) when webpush is mocked.
- Dedup prevents the same (entry_id, ladder_step) from firing twice.
- Ladder resets cleanly when the singer's next-closest entry changes
  (e.g. first song Done'd, the second in the queue takes over as target).
"""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


def _collect_steps(mock_webpush):
    """Extract ladder step names from the sequence of mocked webpush calls."""
    steps = []
    for call in mock_webpush.call_args_list:
        data = call.kwargs.get("data")
        if data is None and len(call.args) >= 2:
            data = call.args[1]
        if data is None:
            continue
        payload = json.loads(data)
        steps.append(payload["data"]["step"])
    return steps


def _drain(dispatcher):
    """Let the debounce timer fire and the executor drain."""
    # Cancel any pending debounce timer, then forcibly invoke dispatch-now
    # to bypass the 0.5s debounce for deterministic tests.
    with dispatcher._debounce_lock:
        if dispatcher._debounce_timer is not None:
            dispatcher._debounce_timer.cancel()
            dispatcher._debounce_timer = None
    dispatcher._dispatch_now()
    dispatcher.executor.shutdown(wait=True)
    # Re-create the executor so later mutations can still submit
    dispatcher.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="push-")


def test_full_push_ladder_flow(sing_app, token):
    """Singer subscribes, entry moves through positions 3→2→Now Singing,
    receives up_in_2, up_next, now_singing pushes (no dupes)."""
    sing_app.sing_store.set_enabled(True)

    # Step 1: submit Alice's request + create matching rotation entry + link
    req = sing_app.sing_store.create_request(
        singer_name="Alice", phone="+61400000001",
        song_artist="Queen", song_title="Somebody to Love",
        source_type="make", source_ref=None, source_meta=None, notes="",
    )
    alice_entry = sing_app.rotation.add_entry(
        "Alice", song_artist="Queen — Somebody to Love",
    )
    sing_app.sing_store.mark_approved(req["id"], linked_entry_id=alice_entry["id"])

    # Step 2: Alice's device subscribes
    sing_app.sing_store.insert_push_subscription(
        token=token, phone="+61400000001", singer_name="Alice",
        endpoint="alice-ep", p256dh="p", auth="a",
    )

    # Step 3: add filler entries ahead of Alice
    sing_app.rotation.add_entry("FillerA", song_artist="Filler 1")
    sing_app.rotation.add_entry("FillerB", song_artist="Filler 2")

    # Step 4: ensure Alice is at position 3 (2 ahead) — add_entry appends to end,
    # so after 3 adds Alice is at position 1 (first added), fillers at 2,3.
    # Move Alice to position 3.
    sing_app.rotation.move_entry(alice_entry["id"], 3)

    # Verify initial state
    entries = sing_app.rotation.get_rotation()
    active = [e for e in entries if e["status"].lower() not in ("done", "left")]
    alice_pos = next(i for i, e in enumerate(active) if e["id"] == alice_entry["id"]) + 1
    assert alice_pos == 3, f"Alice should be at position 3, got {alice_pos}"

    with patch("push_dispatcher.webpush") as wp:
        dispatcher = sing_app.rotation.push_dispatcher

        # Dispatch at pos 3 → up_in_2 push
        _drain(dispatcher)
        steps_after_pos_3 = _collect_steps(wp)
        assert "up_in_2" in steps_after_pos_3

        # Move Alice to pos 2 → should fire up_next (new ladder step)
        sing_app.rotation.move_entry(alice_entry["id"], 2)
        _drain(dispatcher)
        steps_after_pos_2 = _collect_steps(wp)
        assert "up_next" in steps_after_pos_2

        # Move Alice to pos 1 → still up_next (dedup blocks: same entry+step)
        sing_app.rotation.move_entry(alice_entry["id"], 1)
        _drain(dispatcher)
        steps_after_pos_1 = _collect_steps(wp)
        assert steps_after_pos_1.count("up_next") == 1  # no duplicate push

        # Mark Now Singing → now_singing push
        sing_app.rotation.mark_singing(alice_entry["id"])
        _drain(dispatcher)
        final_steps = _collect_steps(wp)
        assert "now_singing" in final_steps

        # Full sequence: up_in_2 then up_next then now_singing, each exactly once
        assert final_steps.count("up_in_2") == 1
        assert final_steps.count("up_next") == 1
        assert final_steps.count("now_singing") == 1


def test_ladder_resets_on_second_song(sing_app, token):
    """Alice has 2 songs. First Done'd → second becomes the target → ladder resets."""
    sing_app.sing_store.set_enabled(True)

    # First song — submit + link
    req1 = sing_app.sing_store.create_request(
        singer_name="Alice", phone="+61400000001",
        song_artist="Queen", song_title="Somebody to Love",
        source_type="make", source_ref=None, source_meta=None, notes="",
    )
    entry1 = sing_app.rotation.add_entry(
        "Alice", song_artist="Queen — Somebody to Love",
    )
    sing_app.sing_store.mark_approved(req1["id"], linked_entry_id=entry1["id"])

    # Second song — also Alice
    req2 = sing_app.sing_store.create_request(
        singer_name="Alice", phone="+61400000001",
        song_artist="Pink Floyd", song_title="Money",
        source_type="make", source_ref=None, source_meta=None, notes="",
    )
    entry2 = sing_app.rotation.add_entry(
        "Alice", song_artist="Pink Floyd — Money",
    )
    sing_app.sing_store.mark_approved(req2["id"], linked_entry_id=entry2["id"])

    # Alice subscribes
    sing_app.sing_store.insert_push_subscription(
        token=token, phone="+61400000001", singer_name="Alice",
        endpoint="alice-ep", p256dh="p", auth="a",
    )

    # Put entry1 at position 1, entry2 further back
    filler = sing_app.rotation.add_entry("Filler", song_artist="Filler")
    # Current order: entry1, entry2, filler. Move filler between them:
    sing_app.rotation.move_entry(filler["id"], 2)
    # Now order: entry1, Filler, entry2

    # Verify ordering
    entries = sing_app.rotation.get_rotation()
    active = [e for e in entries if e["status"].lower() not in ("done", "left")]
    entry1_pos = next(i for i, e in enumerate(active) if e["id"] == entry1["id"]) + 1
    assert entry1_pos == 1, f"entry1 should be at position 1, got {entry1_pos}"

    with patch("push_dispatcher.webpush") as wp:
        dispatcher = sing_app.rotation.push_dispatcher
        _drain(dispatcher)
        # entry1 at pos 1 → up_next
        assert "up_next" in _collect_steps(wp)

        # Mark entry1 Done — ladder resets, entry2 becomes next target
        sing_app.rotation.update_status(entry1["id"], "Done")
        _drain(dispatcher)

        # entry2 is now at position 2 (after Filler), so it triggers up_next
        # fresh (different entry_id from last_sent_state)
        all_steps = _collect_steps(wp)
        # Expect 2 up_next entries total: one for entry1, one for entry2
        assert all_steps.count("up_next") == 2
