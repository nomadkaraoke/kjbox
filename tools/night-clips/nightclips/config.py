"""User config at ``~/.night-clips/config.toml`` (created with defaults on first run)."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

HOME = Path(os.environ.get("NIGHT_CLIPS_HOME", Path.home() / ".night-clips"))

DEFAULT_CONFIG_TOML = """\
# night-clips config. Edit freely; delete this file to get the defaults back.

# Where the phone's videos land (Dropbox Camera Uploads).
camera_uploads = "~/AB Dropbox/Andrew Beveridge/Camera Uploads"

# ssh host alias for the kjbox (NomadPC). Must reach localhost:5001 on the box.
kjbox_host = "nomadpctunnel"

# Optional: search text for the Facebook "Check in" location (e.g. "The Babylon Kino"). Empty = skip.
facebook_location = ""

# Instagram allows up to 20 items in one carousel.
max_clips = 20

[caption]
# Placeholders: {date_pretty} (e.g. "October 8"), {date} (2026-10-08), {weekday} (Thursday)
header = "Thursday night karaoke 🎤 {date_pretty}"
# One line per selected song. {performers} = "@handle" or "Name", joined with " & ".
line = "{performers} — {song}"
footer = "#karaoke #nomadkaraoke"

[handles]
# Instagram handles for regulars who didn't add one in the singer UI.
# "Name as they sign up" = "handle" (the @ and instagram.com/ links are fine).
# Names match like consent does (case and extra spaces don't matter). A handle the
# singer gave on the night wins, and nobody is tagged unless they said yes to photos.
# "Big Dave" = "davethevoice"      (an empty "" means no handle yet)
"""

DEFAULTS = tomllib.loads(DEFAULT_CONFIG_TOML)


def load_config(home: Path | None = None) -> dict:
    """Read the config, writing the default file first if it doesn't exist."""
    home = home or HOME
    path = home / "config.toml"
    if not path.exists():
        home.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    user = tomllib.loads(path.read_text(encoding="utf-8"))
    cfg = {**DEFAULTS, **{k: v for k, v in user.items() if k not in ("caption", "handles")}}
    cfg["caption"] = {**DEFAULTS["caption"], **(user.get("caption") or {})}
    cfg["camera_uploads"] = str(Path(os.path.expanduser(cfg["camera_uploads"])))
    cfg["max_clips"] = max(1, min(20, int(cfg.get("max_clips") or 20)))
    cfg["handles"] = _load_handles(user.get("handles") or {}, path)
    cfg["path"] = str(path)
    return cfg


_HANDLE_RE = re.compile(r"^[a-z0-9._]{1,30}$")
_HANDLE_URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?instagram\.com/([^/?#]+)/?(?:[?#].*)?$", re.I)


def normalize_handle(raw: str) -> str | None:
    """"@Name", "instagram.com/name" or "name" -> "name"; None if it isn't a valid handle."""
    h = (raw or "").strip()
    m = _HANDLE_URL_RE.match(h)
    if m:
        h = m.group(1)
    h = h.lstrip("@").lower()
    return h if _HANDLE_RE.match(h) else None


def _load_handles(table: dict, path: Path) -> dict[str, str]:
    """``{name_key: handle}`` from the ``[handles]`` table. Bad entries fail loudly."""
    out: dict[str, str] = {}
    for name, raw in table.items():
        if isinstance(raw, str) and not raw.strip():
            continue  # placeholder: no handle known yet
        handle = normalize_handle(raw) if isinstance(raw, str) else None
        if not handle:
            raise SystemExit(f"ERROR: {path} [handles]: {name!r} = {raw!r} isn't a valid Instagram handle")
        out[" ".join(name.casefold().split())] = handle
    return out
