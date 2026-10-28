"""User config at ``~/.night-clips/config.toml`` (created with defaults on first run)."""

from __future__ import annotations

import os
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
    cfg = {**DEFAULTS, **{k: v for k, v in user.items() if k != "caption"}}
    cfg["caption"] = {**DEFAULTS["caption"], **(user.get("caption") or {})}
    cfg["camera_uploads"] = str(Path(os.path.expanduser(cfg["camera_uploads"])))
    cfg["max_clips"] = max(1, min(20, int(cfg.get("max_clips") or 20)))
    cfg["path"] = str(path)
    return cfg
