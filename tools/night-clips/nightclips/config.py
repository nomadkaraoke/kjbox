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


_HANDLE_LINE_RE = re.compile(r'^(\s*)("(?:[^"\\]|\\.)*"|\'[^\']*\'|[A-Za-z0-9_-]+)(\s*=\s*)("(?:[^"\\]|\\.)*"|\'[^\']*\')(.*)$')


def _toml_key(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_handle(path: Path, name: str, handle: str) -> None:
    """Set ``name``'s entry in the ``[handles]`` table of ``path``, keeping every comment and line.

    Updates each existing entry whose name matches (the way consent matches: case and spaces
    ignored), or appends one at the end of the table. ``handle`` "" writes the "no handle yet"
    placeholder. The file is re-parsed before it replaces the old one, so a bad write never lands.
    """
    text = path.read_text(encoding="utf-8") if path.exists() else DEFAULT_CONFIG_TOML
    lines = text.splitlines(keepends=True)
    key = " ".join(name.casefold().split())
    value = '"' + handle + '"'  # normalize_handle output is [a-z0-9._] only
    section, start, end, hit = None, None, len(lines), False
    for i, line in enumerate(lines):
        stripped = line.split("#", 1)[0].strip()  # "[handles]   # regulars" is still the handles table
        if stripped.startswith("[") and not stripped.startswith("[["):
            if section == "handles" and end == len(lines):
                end = i
            section = stripped.strip("[]").strip().strip("\"'")
            if section == "handles":
                start, end = i, len(lines)
            continue
        if section != "handles":
            continue
        m = _HANDLE_LINE_RE.match(line.rstrip("\r\n"))
        if not m:
            continue
        try:
            raw_key = next(iter(tomllib.loads(f"{m.group(2)} = 1")))
        except (tomllib.TOMLDecodeError, StopIteration):
            continue
        if " ".join(raw_key.casefold().split()) == key:
            nl = line[len(line.rstrip("\r\n")):]
            lines[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}{value}{m.group(5)}{nl}"
            hit = True
    if not hit:
        entry = f"{_toml_key(name)} = {value}\n"
        if start is None:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines += ["\n", "[handles]\n", entry]
        else:
            # after the table's last non-blank line, so it sits with the other entries
            at = end
            while at - 1 > start and not lines[at - 1].strip():
                at -= 1
            if at > 0 and not lines[at - 1].endswith("\n"):
                lines[at - 1] += "\n"
            lines.insert(at, entry)
    new = "".join(lines)
    tomllib.loads(new)  # never write a file that won't load
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(new, encoding="utf-8")
    tmp.replace(path)
