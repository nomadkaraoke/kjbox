"""Integration tests for the Auto Order wiring:

* ``POST /rotation/auto-order`` (the on-demand header button), and
* the auto-reorder-on-new-entry trigger gated by the ``auto_reorder`` setting.

These exercise the full stack — decorate → build_entry_views → compute_auto_order →
reorder_by_ids — against a real rotation, not the pure algorithm (that's covered in
tests/unit/test_auto_order.py).
"""

import pytest

from app import create_app


@pytest.fixture
def app(mock_config):
    app = create_app(config=mock_config)
    app.config["TESTING"] = True
    yield app
    app.catalog.close()


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


def _seed_veterans_then_newbie(app, n_vets=10):
    """Build a queue where each veteran has already sung once (so they rank below a
    brand-new singer), then append a brand-new singer at the very bottom. Returns the
    new singer's rotation entry id."""
    r = app.rotation
    for i in range(n_vets):
        e = r.add_entry(f"Vet{i}", f"done{i}")
        r.update_status(e["id"], "Done")          # Vet{i} sung == 1
    for i in range(n_vets):
        r.add_entry(f"Vet{i}", f"queued{i}")       # their waiting songs
    return r.add_entry("Newbie", "newbie song")["id"]


class TestAutoOrderEndpoint:
    def test_reorders_and_keeps_rows_1_to_3_frozen(self, app, client):
        new_id = _seed_veterans_then_newbie(app)
        before = [e["id"] for e in app.rotation.get_rotation()]
        assert before[-1] == new_id  # starts at the bottom

        resp = client.post("/rotation/auto-order", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["changed"] is True

        after = [e["id"] for e in data["entries"]]
        # Same set of entries, rows 1-3 untouched, new singer pulled up off the bottom.
        assert sorted(after) == sorted(before)
        assert after[:3] == before[:3]
        assert after.index(new_id) < len(after) - 1

    def test_noop_on_already_fair_queue_reports_unchanged(self, app, client):
        r = app.rotation
        for i in range(4):
            r.add_entry(f"S{i}", f"song{i}")       # 4 distinct new singers, already fair
        resp = client.post("/rotation/auto-order", json={})
        assert resp.status_code == 200
        assert resp.get_json()["changed"] is False

    def test_503_when_rotation_absent(self, app, client):
        app.rotation = None
        resp = client.post("/rotation/auto-order", json={})
        assert resp.status_code == 503


class TestAutoReorderTrigger:
    def test_disabled_by_default_new_entry_stays_at_bottom(self, app, client):
        _seed_veterans_then_newbie(app)
        assert app.sing_store.is_auto_reorder() is False
        resp = client.post("/rotation/add", json={"singer": "Latecomer", "song_artist": "x"})
        assert resp.status_code == 200
        entries = resp.get_json()["entries"]
        # No auto-reorder → the just-added singer is still last.
        assert entries[-1]["singer"] == "Latecomer"

    def test_enabled_new_entry_is_auto_reordered_up(self, app, client):
        _seed_veterans_then_newbie(app)
        app.sing_store.set_auto_reorder(True)
        resp = client.post("/rotation/add", json={"singer": "Latecomer", "song_artist": "x"})
        assert resp.status_code == 200
        entries = resp.get_json()["entries"]
        # Auto-reorder fired → the new singer was pulled up off the bottom.
        idx = [e["singer"] for e in entries].index("Latecomer")
        assert idx < len(entries) - 1

    def test_trigger_failure_never_blocks_the_add(self, app, client, monkeypatch):
        # Even if reordering blows up, the add must still succeed (best-effort).
        app.sing_store.set_auto_reorder(True)
        import routes
        monkeypatch.setattr(routes, "run_auto_order",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        resp = client.post("/rotation/add", json={"singer": "Zed", "song_artist": "x"})
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True


class TestSetPriorityEndpoint:
    def test_bump_up_persists_and_reweaves(self, app, client):
        new_id = _seed_veterans_then_newbie(app)
        # A veteran's queued song sitting low in the queue.
        low = app.rotation.get_rotation()[-2]["id"]
        resp = client.post("/rotation/set-priority", json={"id": low, "bias": 1})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        # The bias persisted on the entry...
        biased = next(e for e in data["entries"] if e["id"] == low)
        assert biased["priority_bias"] == 1
        # ...and the response carries the standard re-weave payload.
        assert "singer_stats" in data and "history" in data and "rev" in data

    def test_bump_up_is_a_single_undoable_step(self, app, client):
        _seed_veterans_then_newbie(app)
        low = app.rotation.get_rotation()[-2]["id"]
        client.post("/rotation/set-priority", json={"id": low, "bias": 1})
        # One undo clears the bias (part of the same checkpoint).
        app.rotation.undo()
        assert app.rotation.store.get_entry(low)["priority_bias"] == 0

    def test_rejects_bad_bias(self, client):
        resp = client.post("/rotation/set-priority", json={"id": 1, "bias": 2})
        assert resp.status_code == 400

    def test_rejects_boolean_bias(self, client):
        resp = client.post("/rotation/set-priority", json={"id": 1, "bias": True})
        assert resp.status_code == 400

    def test_missing_id(self, client):
        resp = client.post("/rotation/set-priority", json={"bias": 1})
        assert resp.status_code == 400

    def test_unknown_entry_is_404_without_leaking_a_checkpoint(self, app, client):
        app.rotation.add_entry("Solo", "s")  # ensure rotation is configured
        undo_before = app.rotation.history_status()["undo"]
        resp = client.post("/rotation/set-priority", json={"id": 99999, "bias": 1})
        assert resp.status_code == 404
        # A failed (unknown-id) set must not leave an undo checkpoint behind.
        assert app.rotation.history_status()["undo"] == undo_before

    def test_503_when_rotation_absent(self, app, client):
        app.rotation = None
        resp = client.post("/rotation/set-priority", json={"id": 1, "bias": 1})
        assert resp.status_code == 503


class TestSingerPriorityEndpoint:
    def test_biases_every_entry_for_the_singer(self, app, client):
        r = app.rotation
        a1 = r.add_entry("Dave", "one")
        a2 = r.add_entry("Dave", "two")
        r.add_entry("Erin", "e")
        resp = client.post("/rotation/singer/priority", json={"name": "Dave", "bias": -1})
        assert resp.status_code == 200
        assert r.store.get_entry(a1["id"])["priority_bias"] == -1
        assert r.store.get_entry(a2["id"])["priority_bias"] == -1

    def test_requires_name(self, client):
        resp = client.post("/rotation/singer/priority", json={"bias": 1})
        assert resp.status_code == 400

    def test_rejects_bad_bias(self, app, client):
        app.rotation.add_entry("Dave", "one")
        resp = client.post("/rotation/singer/priority", json={"name": "Dave", "bias": 7})
        assert resp.status_code == 400


class TestAutoReorderConfig:
    def test_get_config_exposes_auto_reorder_default_off(self, client):
        resp = client.get("/rotation/requests/config")
        assert resp.status_code == 200
        assert resp.get_json()["auto_reorder"] is False

    def test_toggle_on(self, app, client):
        resp = client.post("/rotation/requests/config", json={"auto_reorder": True})
        assert resp.status_code == 200
        assert resp.get_json()["changed"]["auto_reorder"] is True
        assert app.sing_store.is_auto_reorder() is True

    def test_rejects_non_boolean(self, client):
        resp = client.post("/rotation/requests/config", json={"auto_reorder": "true"})
        assert resp.status_code == 400
