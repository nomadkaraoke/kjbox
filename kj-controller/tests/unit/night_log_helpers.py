"""Row helpers shared by the Night Log and history-claim tests: they write
rotation_archive / sing_requests / play_events rows the way the box does."""


def _legacy_archive(store, singer, song, file_path, created_at, status="Done", night_date="2026-10-09"):
    """Insert an archive row the way pre-night-log archives looked (no entry id)."""
    conn = store._get_conn()
    conn.execute(
        "INSERT INTO rotation_archive (night_date, singer, song_artist, status, notes, "
        "position, file_path, duration, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (night_date, singer, song, status, "", 0, file_path, None, created_at))
    conn.commit()


def _request(store, singer, artist, title, source_type, reviewed_at, linked_entry_id=None,
             status="approved"):
    conn = store._get_conn()
    conn.execute(
        "INSERT INTO sing_requests (created_at, token, singer_name, phone, song_artist, "
        "song_title, source_type, status, reviewed_at, linked_entry_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (reviewed_at, "tok", singer, "+15550000000", artist, title, source_type, status,
         reviewed_at, linked_entry_id))
    conn.commit()


def _play(stats, media_id, entry_id, singer, played_utc, artist=None, title=None):
    conn = stats._get_conn()
    conn.execute(
        "INSERT INTO play_events (media_id, singer, singer_norm, played_at, night_date, "
        "entry_id, source, artist, title) VALUES (?,?,?,?,?,?, 'live',?,?)",
        (media_id, singer, (singer or "").lower(), played_utc, played_utc[:10], entry_id,
         artist, title))
    conn.commit()


def _exact_archive(store, entry_id, singer, song, created_at, done_at, file_path=None,
                   singers_json=None, status="Done", night_date="2026-10-20"):
    """Insert an archive row the way archives look since the Night Log shipped."""
    conn = store._get_conn()
    conn.execute(
        "INSERT INTO rotation_archive (night_date, singer, song_artist, status, notes, "
        "position, file_path, duration, created_at, entry_id, done_at, singers_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (night_date, singer, song, status, "", 0, file_path, None, created_at, entry_id,
         done_at, singers_json))
    conn.commit()


def _utc_of_local(local):
    """Convert a local 'YYYY-MM-DD HH:MM:SS' to the UTC string play_events stores."""
    import datetime as dt
    t = dt.datetime.strptime(local, "%Y-%m-%d %H:%M:%S")
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
