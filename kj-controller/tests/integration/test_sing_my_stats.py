"""Tests for GET /sing/my-stats — the search screen's "sung here before?"
inspiration section (singer's own play history + venue crowd favourites)."""


def _seed_play(stats, singer, artist, title, entry_id, media_id="m1"):
    stats.record_play(
        media_id, entry_id=entry_id, singer=singer, artist=artist, title=title,
        song_key=f"{artist}|{title}".lower(), source="live")


class TestMyStats:
    def test_requires_token(self, client):
        assert client.get("/sing/my-stats?name=Andrew").status_code == 403

    def test_empty_history(self, client, token):
        resp = client.get(f"/sing/my-stats?name=Nobody&t={token}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["my_songs"] == []
        assert data["top_songs"] == []

    def test_returns_singer_history_and_top_songs(self, client, sing_app, token):
        stats = sing_app.stats
        # Andrew sang Books From Boxes twice, My Hero once; Jen sang My Hero.
        _seed_play(stats, "Andrew", "Maximo Park", "Books From Boxes", 1)
        _seed_play(stats, "Andrew", "Maximo Park", "Books From Boxes", 2)
        _seed_play(stats, "Andrew", "Foo Fighters", "My Hero", 3, media_id="m2")
        _seed_play(stats, "Jen", "Foo Fighters", "My Hero", 4, media_id="m2")

        resp = client.get(f"/sing/my-stats?name=Andrew&t={token}")
        assert resp.status_code == 200
        data = resp.get_json()

        mine = {(s["artist"], s["title"]): s["plays"] for s in data["my_songs"]}
        assert mine == {("Maximo Park", "Books From Boxes"): 2,
                        ("Foo Fighters", "My Hero"): 1}
        assert all("last_sung" in s for s in data["my_songs"])
        # Jen's solo plays don't leak into Andrew's list, but do count in top.
        top = {(s["artist"], s["title"]): s["plays"] for s in data["top_songs"]}
        assert top[("Foo Fighters", "My Hero")] == 2

    def test_name_matching_is_normalized(self, client, sing_app, token):
        _seed_play(sing_app.stats, "Sarah B.", "Fleetwood Mac", "Dreams", 10)
        resp = client.get(f"/sing/my-stats?name=sarah%20b.&t={token}")
        songs = resp.get_json()["my_songs"]
        assert len(songs) == 1
        assert songs[0]["title"] == "Dreams"

    def test_no_name_returns_top_only(self, client, sing_app, token):
        _seed_play(sing_app.stats, "Andrew", "Billy Joel", "Piano Man", 20)
        resp = client.get(f"/sing/my-stats?t={token}")
        data = resp.get_json()
        assert data["my_songs"] == []
        assert len(data["top_songs"]) >= 1
