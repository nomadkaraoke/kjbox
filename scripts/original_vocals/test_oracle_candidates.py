import os
from classify import AudioFile
from oracle_candidates import filter_candidates, enumerate_candidates


def af(name, ext, size=1000, path=None):
    return AudioFile(size=size, path=path or f"F/{name}", name=name, ext=ext)


def test_filter_drops_existing_stems_and_renders_keeps_originals():
    files = [
        af("Idlewild - Little Discourage.mp3", "mp3"),  # CDG-backfill instrumental (KEEP: oracle judges)
        af("01 Little Discourage.flac", "flac"),  # album rip original (KEEP)
        af("01 Little Discourage_(Vocals)_2_HP-UVR.flac", "flac"),  # existing stem (DROP)
        af("01 Little Discourage_(Instrumental)_mel_band.flac", "flac"),  # existing stem (DROP)
        af("Idlewild - Little Discourage (Karaoke) [abc].webm", "webm"),  # render (DROP)
    ]

    kept = {f.name for f in filter_candidates(files)}
    assert kept == {"Idlewild - Little Discourage.mp3", "01 Little Discourage.flac"}


def test_enumerate_reads_folder_recursively(tmp_path):
    # Create nested structure with audio and non-audio files
    (tmp_path / "subdir").mkdir()

    # Create test files
    (tmp_path / "original.mp3").write_text("dummy")
    (tmp_path / "song_(Vocals)_UVR.flac").write_text("dummy")  # excluded
    (tmp_path / "track.karaoke.webm").write_text("dummy")  # excluded
    (tmp_path / "readme.txt").write_text("dummy")  # non-audio
    (tmp_path / "subdir" / "nested.wav").write_text("dummy")

    results = enumerate_candidates(str(tmp_path))
    names = {f.name for f in results}

    # Should only keep non-excluded audio files
    assert names == {"original.mp3", "nested.wav"}
