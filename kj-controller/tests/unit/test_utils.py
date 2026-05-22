"""Tests for utility functions: sanitize_filename_part, parse_youtube_filename."""

from utils import (
    sanitize_filename_part,
    parse_youtube_filename,
    log_message,
    build_divebar_filename,
)


def test_sanitize_filename_part_removes_unsafe_chars():
    result = sanitize_filename_part('hello<world>:test')
    assert '<' not in result
    assert '>' not in result
    assert ':' not in result


def test_sanitize_filename_part_replaces_double_underscores():
    result = sanitize_filename_part('hello__world')
    assert '__' not in result


def test_sanitize_filename_part_collapses_whitespace():
    result = sanitize_filename_part('hello   world')
    assert result == 'hello world'


def test_sanitize_filename_part_truncates_to_100_chars():
    long_text = 'a' * 200
    result = sanitize_filename_part(long_text)
    assert len(result) <= 100


def test_sanitize_filename_part_strips_whitespace():
    result = sanitize_filename_part('  hello  ')
    assert result == 'hello'


def test_sanitize_filename_part_empty_string():
    result = sanitize_filename_part('')
    assert result == ''


def test_parse_youtube_filename_valid():
    result = parse_youtube_filename('dQw4w9WgXcQ__RickAstley__Never Gonna Give You Up.mp4')
    assert result == ('dQw4w9WgXcQ', 'RickAstley', 'Never Gonna Give You Up')


def test_parse_youtube_filename_non_youtube():
    result = parse_youtube_filename('random_song.mp4')
    assert result is None


def test_parse_youtube_filename_wrong_id_length():
    result = parse_youtube_filename('short__channel__title.mp4')
    assert result is None


def test_parse_youtube_filename_multiple_separators():
    result = parse_youtube_filename('dQw4w9WgXcQ__MyChannel__Title__With__Extras.mp4')
    assert result == ('dQw4w9WgXcQ', 'MyChannel', 'Title__With__Extras')


def test_log_message_prints(capsys):
    """log_message prints to stderr for systemd journal visibility."""
    log_message("test output")
    captured = capsys.readouterr()
    assert "test output" in captured.err


def test_log_message_writes_to_file(tmp_path):
    """log_message writes to log file when config is provided."""
    log_file = tmp_path / "test.log"
    cfg = {"log_file": str(log_file)}
    log_message("file output", config=cfg)
    assert "file output" in log_file.read_text()


def test_build_divebar_filename_all_fields():
    assert build_divebar_filename("WTF", "Queen", "Bohemian Rhapsody") == \
        "WTF - Queen - Bohemian Rhapsody.mp4"


def test_build_divebar_filename_missing_brand_code_uses_db():
    assert build_divebar_filename(None, "Queen", "Bohemian Rhapsody") == \
        "DB - Queen - Bohemian Rhapsody.mp4"


def test_build_divebar_filename_empty_brand_code_uses_db():
    assert build_divebar_filename("", "Queen", "Bohemian Rhapsody") == \
        "DB - Queen - Bohemian Rhapsody.mp4"


def test_build_divebar_filename_missing_artist():
    assert build_divebar_filename("WTF", None, "Bohemian Rhapsody") == \
        "WTF - Bohemian Rhapsody.mp4"


def test_build_divebar_filename_missing_title():
    assert build_divebar_filename("WTF", "Queen", None) == \
        "WTF - Queen.mp4"


def test_build_divebar_filename_only_brand_returns_none():
    assert build_divebar_filename("WTF", None, None) is None
    assert build_divebar_filename(None, None, None) is None
    assert build_divebar_filename("WTF", "", "") is None


def test_build_divebar_filename_sanitizes_unsafe_chars():
    result = build_divebar_filename("WTF", "Queen/Bowie", "Under Pressure")
    assert "/" not in result
    assert result.endswith(".mp4")


def test_build_divebar_filename_custom_extension():
    assert build_divebar_filename("WTF", "Queen", "Song", ext=".zip") == \
        "WTF - Queen - Song.zip"
