"""Unit tests for media.relocate_download_sidecars.

Downloads used to keep yt-dlp's `.webp` thumbnail next to every mp4 (litter the
app never uses). The tidy step now deletes image thumbnails and moves any other
sidecar (e.g. .info.json) next to the final video.
"""
import os

import media


def _touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def test_thumbnail_webp_is_deleted(tmp_path):
    dl = tmp_path / "downloads"
    dest = tmp_path / "downloads" / "youtube"
    old_stem = "Artist - Song [yt-abc]"
    _touch(str(dl / f"{old_stem}.webp"))

    media.relocate_download_sidecars(str(dl), old_stem, str(dest), "Artist - Song [yt-abc]")

    assert not (dl / f"{old_stem}.webp").exists()
    # And it wasn't just moved into the dest dir either.
    assert not (dest / "Artist - Song [yt-abc].webp").exists()


def test_other_image_sidecars_deleted(tmp_path):
    dl = tmp_path / "downloads"
    dest = dl / "youtube"
    old_stem = "S"
    for ext in (".jpg", ".jpeg", ".png", ".gif"):
        _touch(str(dl / f"{old_stem}{ext}"))

    media.relocate_download_sidecars(str(dl), old_stem, str(dest), "S")

    for ext in (".jpg", ".jpeg", ".png", ".gif"):
        assert not (dl / f"{old_stem}{ext}").exists()


def test_non_image_sidecar_is_moved_next_to_video(tmp_path):
    dl = tmp_path / "downloads"
    dest = dl / "youtube"
    dest.mkdir(parents=True)
    old_stem = "Artist - Song [yt-abc]"
    _touch(str(dl / f"{old_stem}.info.json"), b"{}")

    media.relocate_download_sidecars(str(dl), old_stem, str(dest), "Artist - Song [yt-abc]")

    assert not (dl / f"{old_stem}.info.json").exists()
    assert (dest / "Artist - Song [yt-abc].info.json").exists()


def test_unrelated_files_untouched(tmp_path):
    dl = tmp_path / "downloads"
    dest = dl / "youtube"
    _touch(str(dl / "Someone Else - Other [yt-zzz].webp"))  # different stem

    media.relocate_download_sidecars(str(dl), "Artist - Song [yt-abc]", str(dest), "x")

    assert (dl / "Someone Else - Other [yt-zzz].webp").exists()


def test_missing_download_folder_does_not_raise(tmp_path):
    # Best-effort: a vanished folder must not blow up the download flow.
    media.relocate_download_sidecars(str(tmp_path / "nope"), "stem", str(tmp_path), "stem")
