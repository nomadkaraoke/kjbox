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
    d = tmp_path / "NOMAD-0100 - Idlewild - Little Discourage"
    (d / "sub").mkdir(parents=True)
    (d / "01 Little Discourage.flac").write_bytes(b"x" * 10)
    (d / "sub" / "Idlewild - Little Discourage.mp3").write_bytes(b"y" * 20)
    (d / "cover.jpg").write_bytes(b"z")            # non-audio ignored
    (d / "song_(Vocals)_2_HP-UVR.flac").write_bytes(b"v")  # stem dropped
    got = {os.path.basename(c.path) for c in enumerate_candidates(str(d))}
    assert got == {"01 Little Discourage.flac", "Idlewild - Little Discourage.mp3"}
