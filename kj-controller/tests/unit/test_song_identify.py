"""On-device song identification (docs/SONG-IDENTIFICATION.md) over a tiny index.

The real index (~2M songs) is built from a BigQuery export and evaluated with
scripts/song_id_eval.py; these tests pin the matcher's behaviour on the kinds of
queries singers actually type, using a small hand-made catalogue."""
import gzip

import pytest

from scripts.build_song_id_db import build
from song_identify import SongIdentifier, song_norm

SONGS = [
    # artist, title, popularity, karaoke
    ("The Strokes", "Machu Picchu", 64, 1),
    ("The Strokes", "Machu Piccu", 30, 0),           # MusicBrainz misspelled duplicate
    ("The Strokes", "The Adults Are Talking", 70, 1),
    ("The Strokes", "Reptilia", 72, 1),
    ("The Stokes", "Lonely Road", 20, 0),            # real obscure band: must not steal "the stokes"
    ("Evaluna Montaner", "Machu Picchu", 72, 0),
    ("Rihanna", "Push Up On Me", 47, 0),
    ("Rihanna", "Umbrella", 85, 1),
    ("Sabrina Carpenter", "Espresso", 90, 1),
    ("Sabrina Carpenter", "Buy Me Presents", 60, 1),
    ("Laza Bossa", "Espresso", 62, 0),
    ("Kesha", "Die Young", 81, 1),
    ("Chappell Roan", "Die Young", 61, 1),
    ("Miley Cyrus", "Party In The U.S.A.", 80, 1),
    ("Seal", "Kiss from a Rose", 75, 1),
    ("Mother Mother", "Hayloft", 73, 1),
    ("Mother Mother", "Hayloft II", 70, 0),
    ("Soft Cell", "Tainted Love", 78, 1),
    ("Eric Carmen", "All By Myself", 60, 1),
    ("Dan + Shay", "All To Myself", 65, 0),
    ("Leonard Cohen", "Hallelujah", 70, 1),
    ("Jeff Buckley", "Hallelujah", 72, 1),
    ("Maximo Park", "Books From Boxes", 53, 1),
    ("Narrow Head", "See You Around", "", 1),         # karaoke-only row (no Spotify popularity)
    ("Taylor Swift", "my tears ricochet", 80, 1),
    ("Charlie Rich", "Too Many Tears", 30, 0),
    ("Sara Bareilles", "She Used to Be Mine", 68, 1),
    ("The Used", "Tell Me", 35, 0),
    ("Rihanna", "Breakin' Dishes", 70, 1),
    ("Rihanna", "Breaking Dishes (Soul Seekerz club mix)", 50, 0),
]


@pytest.fixture(scope="module")
def ident(tmp_path_factory):
    d = tmp_path_factory.mktemp("songid")
    src = d / "songs.tsv.gz"
    with gzip.open(src, "wt", encoding="utf-8") as f:
        for a, t, p, k in SONGS:
            f.write(f"{a}\t{t}\t{p}\t{k}\n")
    db = str(d / "song_id.db")
    build(str(src), db)
    return SongIdentifier(db)


def _best(ident, q):
    r = ident.identify(q)
    b = r["best"]
    return r["status"], (b["artist"], b["title"]) if b else None


@pytest.mark.parametrize("q,want", [
    ("rihanna push up on me", ("Rihanna", "Push Up On Me")),            # just casing
    ("push up on me rihanna", ("Rihanna", "Push Up On Me")),            # swapped order
    ("the strokes max picu", ("The Strokes", "Machu Picchu")),          # mangled title
    ("the stokes max picu", ("The Strokes", "Machu Picchu")),           # + artist typo
    ("the stokes adults are talking", ("The Strokes", "The Adults Are Talking")),
    ("Buy me presents Sabrina", ("Sabrina Carpenter", "Buy Me Presents")),  # artist fragment
    ("Seal kiss", ("Seal", "Kiss from a Rose")),                        # partial title + artist
    ("maximo park boks", ("Maximo Park", "Books From Boxes")),
    ("Espresso", ("Sabrina Carpenter", "Espresso")),                    # title only → most popular
    ("Die young", ("Kesha", "Die Young")),
    ("Party in the usa", ("Miley Cyrus", "Party In The U.S.A.")),       # "U.S.A." == "usa"
    ("hayloft mother mother", ("Mother Mother", "Hayloft")),            # not "Hayloft II"
    ("all by myself", ("Eric Carmen", "All By Myself")),                # "by" is a real word
    ("soft cell tainted love", ("Soft Cell", "Tainted Love")),
    ("narrowhead see you around", ("Narrow Head", "See You Around")),   # karaoke-only row
    ("rihanna breaking dishes", ("Rihanna", "Breakin' Dishes")),        # dropped g
])
def test_confident_identifications(ident, q, want):
    assert _best(ident, q) == ("confident", want)


@pytest.mark.parametrize("q,want", [
    ("my tears richo", ("Taylor Swift", "my tears ricochet")),          # mistyped half-typed last word
    ("She used to be mi e", ("Sara Bareilles", "She Used to Be Mine")),  # + a stray space
])
def test_mistyped_partial_last_word_still_finds_the_title(ident, q, want):
    r = ident.identify(q)
    assert r["status"] in ("confident", "candidates")
    assert (r["candidates"][0]["artist"], r["candidates"][0]["title"]) == want


def test_sequel_is_not_merged_with_the_original(ident):
    r = ident.identify("hayloft ii mother mother")
    assert (r["best"]["artist"], r["best"]["title"]) == ("Mother Mother", "Hayloft II")
    assert any(c["title"] == "Hayloft" for c in r["candidates"])


def test_roman_numeral_parts_stay_distinct():
    from song_identify import Match, _same_song
    part = lambda t: Match("Pink Floyd", t, 1.0, 60, True)   # noqa: E731
    assert not _same_song(part("Another Brick in the Wall, Pt. II"), part("Another Brick in the Wall, Pt. III"))
    assert not _same_song(part("Symphony No. 5"), part("Symphony No. 6"))


def test_misspelled_duplicate_does_not_block_confidence(ident):
    r = ident.identify("the stokes max picu")
    assert r["status"] == "confident" and r["best"]["title"] == "Machu Picchu"
    assert all(c["title"] != "Machu Piccu" for c in r["candidates"])


def test_same_title_close_popularity_asks_which_one(ident):
    r = ident.identify("hallelujah")
    assert r["status"] == "candidates"
    assert {c["artist"] for c in r["candidates"][:2]} == {"Jeff Buckley", "Leonard Cohen"}


@pytest.mark.parametrize("q", ["xqzv blorp wibble", "zz", "", "that song from titanic"])
def test_nothing_or_unexplained_query_is_none(ident, q):
    assert ident.identify(q)["status"] == "none"


def test_song_norm_joins_initials_and_keeps_ft_words():
    assert song_norm("Party In The U.S.A.") == "party in the usa"
    assert song_norm("Soft Cell") == "soft cell"
    assert song_norm("Taylor Swift ft. Drake") == "taylor swift"


def test_missing_index_is_unavailable(tmp_path):
    assert SongIdentifier(str(tmp_path / "nope.db")).available is False


def _build_rows(tmp_path, rows):
    src = tmp_path / "songs.tsv.gz"
    with gzip.open(src, "wt", encoding="utf-8") as f:
        for a, t, p, k in rows:
            f.write(f"{a}\t{t}\t{p}\t{k}\n")
    db = str(tmp_path / "song_id.db")
    build(str(src), db)
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT artist, title, pop, karaoke FROM songs ORDER BY artist, title").fetchall()
    finally:
        conn.close()


def test_spelling_variants_merge_and_show_the_karaoke_spelling(tmp_path):
    rows = _build_rows(tmp_path, [
        ("Rihanna", "Breaking Dishes", 55, 0),       # MusicBrainz variant with an ISRC score
        ("Rihanna", "Breakin' Dishes", "", 1),       # the KaraokeNerds / canonical spelling
        ("Smash Mouth", "Walking on the Sun", 40, 0),
        ("Smash Mouth", "Walkin’ on the Sun", 70, 0),
        ("Smash Mouth", "Walkin' On The Sun", "", 1),
        ("Seal", "Kiss From A Rose", "", 1),
        ("Seal", "Kiss from a Rose", 75, 0),         # same key: the most popular spelling shows
    ])
    assert rows == [
        ("Rihanna", "Breakin' Dishes", 55, 1),
        ("Seal", "Kiss from a Rose", 75, 1),
        ("Smash Mouth", "Walkin’ on the Sun", 70, 1),
    ]


def test_between_karaoke_spellings_the_more_popular_wins(tmp_path):
    rows = _build_rows(tmp_path, [
        ("Missy Elliott", "Get Your Freak On", 50, 0),
        ("Missy Elliott", "Get Your Freak On", "", 1),
        ("Missy Elliott", "Get Ur Freak On", 72, 0),
        ("Missy Elliott", "Get Ur Freak On", "", 1),
    ])
    assert rows == [("Missy Elliott", "Get Ur Freak On", 72, 1)]


def test_fold_keeps_different_songs_apart(tmp_path):
    rows = _build_rows(tmp_path, [
        ("Queen", "Bring", 40, 0),                   # 5 letters, folds to "brin" — nothing to meet
        ("Queen", "Sing", 40, 0),                    # short words never fold
        ("Queen", "Sin", 40, 0),
        ("Other", "Breakin' Dishes", 30, 0),         # other artist: never merged
        ("Rihanna", "Breaking Dishes", 55, 0),
    ])
    assert len(rows) == 5
