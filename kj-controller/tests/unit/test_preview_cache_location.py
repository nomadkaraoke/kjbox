"""resolve_preview_cache_dir: the cache lives in a dedicated dir OUTSIDE every
indexed path (sibling of the download folder), so its artifacts are never indexed."""
import os

from config import resolve_preview_cache_dir


def test_default_is_sibling_of_download_folder():
    cfg = {"download_folder": "/opt/nomad/YTDownloads"}
    assert resolve_preview_cache_dir(cfg) == "/opt/nomad/preview-cache"


def test_explicit_dir_wins():
    cfg = {"download_folder": "/opt/nomad/YTDownloads", "preview_cache_dir": "/custom/pc"}
    assert resolve_preview_cache_dir(cfg) == "/custom/pc"


def test_empty_string_falls_back_to_default():
    cfg = {"download_folder": "/opt/nomad/YTDownloads", "preview_cache_dir": ""}
    assert resolve_preview_cache_dir(cfg) == "/opt/nomad/preview-cache"


def test_handles_missing_download_folder():
    expected = os.path.join(os.path.dirname(os.path.realpath("/tmp")), "preview-cache")
    assert resolve_preview_cache_dir({}) == expected
