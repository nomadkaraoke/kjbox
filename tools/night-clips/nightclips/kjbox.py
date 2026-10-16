"""Fetch a night's sung songs + per-performer media consent from the kjbox.

The kjbox (NomadPC) is reached over ``ssh <host> curl localhost:5001/...`` so no
credentials or Cloudflare Access tokens are needed on the Mac. Everything here is
read-only.

Primary source is ``GET /history/night-media`` (see the shared contract in
``docs/superpowers/plans/2026-10-14-instagram-night-clips.md``). When that endpoint
doesn't exist yet (404: the consent PR isn't deployed) we fall back to the Night
Log ``GET /history/night`` and mark every performer's consent ``unknown``, which
means nothing is postable until Andrew decides by hand.
"""

from __future__ import annotations

import datetime as dt
import json
import shlex
import subprocess
from typing import Callable

DEFAULT_HOST = "nomadpctunnel"
DEFAULT_PORT = 5001
SSH_TIMEOUT_S = 60

FALLBACK_WARNING = (
    "kjbox has no /history/night-media endpoint yet (per-song consent not deployed). "
    "Using the Night Log with consent UNKNOWN for every singer: nothing is pre-selected, "
    "and anything you select is on you to have checked."
)

Runner = Callable[..., subprocess.CompletedProcess]


class KjboxUnreachable(RuntimeError):
    """The kjbox could not be reached or returned something unusable."""


def _curl(host: str, path: str, runner: Runner = subprocess.run) -> tuple[int, str]:
    """GET ``path`` on the kjbox via ssh+curl. Returns (http_status, body)."""
    url = f"localhost:{DEFAULT_PORT}{path}"
    remote = f"curl -s -m 30 -w '\\n%{{http_code}}' {shlex.quote(url)}"
    cmd = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, remote]
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=SSH_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise KjboxUnreachable(f"ssh {host} failed: {e}") from e
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()[-1:] or [f"exit {proc.returncode}"]
        raise KjboxUnreachable(f"ssh {host} curl {path} failed: {err[0]}")
    out = proc.stdout or ""
    body, _, status = out.rpartition("\n")
    try:
        return int(status.strip()), body
    except ValueError as e:
        raise KjboxUnreachable(f"unexpected curl output from {host}: {out[-200:]!r}") from e


def split_performers(entry: dict) -> list[str]:
    """Performer names for a Night Log entry: the duet list, else the single singer string.

    Mirrors the contract: composite names like "A & B" without a ``singers`` list stay
    as one performer (the kjbox resolves their parts server-side).
    """
    singers = entry.get("singers")
    if isinstance(singers, list) and singers:
        return [str(s).strip() for s in singers if str(s).strip()]
    name = (entry.get("singer") or "").strip()
    return [name] if name else []


def songs_from_night_log(data: dict) -> list[dict]:
    """Convert a ``/history/night`` response into night-media ``songs`` (consent unknown)."""
    songs = []
    for e in data.get("entries") or []:
        if not e.get("sung_at"):
            continue
        songs.append(
            {
                "entry_id": e.get("entry_id"),
                "song": e.get("song") or "",
                "singer": e.get("singer") or "",
                "sung_at": e["sung_at"],
                "started_at": e.get("started_at"),
                "added_at": e.get("added_at"),
                "performers": [
                    {"name": n, "consent": "unknown", "reason": "unknown", "instagram": None}
                    for n in split_performers(e)
                ],
                "postable": False,
            }
        )
    songs.sort(key=lambda s: s.get("started_at") or s["sung_at"])
    return songs


def normalize_night_media(data: dict) -> list[dict]:
    """Defensive normalisation of a ``/history/night-media`` payload's songs."""
    songs = []
    for s in data.get("songs") or []:
        if not s.get("sung_at"):
            continue
        performers = []
        for p in s.get("performers") or []:
            consent = p.get("consent") if p.get("consent") in ("yes", "no") else "unknown"
            performers.append(
                {
                    "name": p.get("name") or "",
                    "consent": consent,
                    "reason": p.get("reason") or "unknown",
                    "instagram": p.get("instagram") or None,
                }
            )
        if not performers:
            performers = [
                {"name": s.get("singer") or "", "consent": "unknown", "reason": "unknown", "instagram": None}
            ]
        # Never trust a postable flag that disagrees with the performers: every one must be yes.
        postable = bool(s.get("postable")) and all(p["consent"] == "yes" for p in performers)
        songs.append({**s, "performers": performers, "postable": postable})
    songs.sort(key=lambda s: s.get("started_at") or s["sung_at"])
    return songs


def fetch_night(night: str, host: str = DEFAULT_HOST, runner: Runner = subprocess.run) -> dict:
    """Return ``{night, consent_source, songs, warning}`` for a night (YYYY-MM-DD).

    ``consent_source`` is the server's value ("events" / "legacy") or "unavailable"
    when we had to fall back to the Night Log.
    """
    dt.date.fromisoformat(night)  # validate
    status, body = _curl(host, f"/history/night-media?night={night}", runner)
    if status == 200:
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise KjboxUnreachable(f"night-media returned invalid JSON: {e}") from e
        return {
            "night": data.get("night") or night,
            "consent_source": data.get("consent_source") or "events",
            "songs": normalize_night_media(data),
            "warning": None,
        }
    if status != 404:
        raise KjboxUnreachable(f"/history/night-media returned HTTP {status}: {body[:200]}")

    status, body = _curl(host, f"/history/night?night={night}", runner)
    if status != 200:
        raise KjboxUnreachable(f"/history/night returned HTTP {status}: {body[:200]}")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise KjboxUnreachable(f"night log returned invalid JSON: {e}") from e
    return {
        "night": data.get("night") or night,
        "consent_source": "unavailable",
        "songs": songs_from_night_log(data),
        "warning": FALLBACK_WARNING,
    }


def default_night(now: dt.datetime | None = None) -> str:
    """Most recent Thursday whose show has ended (we treat 04:00 Friday as the end).

    Friday daytime -> yesterday. Any time on Thursday or before 04:00 Friday -> the
    previous week's Thursday (tonight's show hasn't finished yet).
    """
    now = now or dt.datetime.now()
    today = now.date()
    thursday = today - dt.timedelta(days=(today.weekday() - 3) % 7)
    show_end = dt.datetime.combine(thursday + dt.timedelta(days=1), dt.time(4, 0))
    if now < show_end:
        thursday -= dt.timedelta(days=7)
    return thursday.isoformat()
