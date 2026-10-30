"""YouTube circuit breaker: stop offering YouTube versions once yt-dlp is failing.

Singers are never warned that a YouTube version "occasionally fails" — we assume
it works. Instead, the first YouTube download that still fails after its
bounded retries with a *transient* error (yt-dlp broken, bot check, network)
trips this breaker. While it's open:

- singer search drops YouTube-only versions (``drop_youtube_only``),
- ``kj_pick`` binding and the download worker prefer local / Divebar versions,
- a background probe re-tries a real download every 30 minutes and closes the
  breaker as soon as one succeeds.

A video-gone error (private/deleted) is about that one video, not yt-dlp, so it
never trips the breaker. Any successful YouTube download closes it.

State lives in ``rotation_meta`` so a kj-controller restart mid-show keeps it.
"""

import json
import threading
import time

import sing_resolve
from utils import log_message

META_KEY = "youtube_breaker"
DEFAULT_PROBE_INTERVAL_S = 30 * 60
# How often the probe thread wakes to check whether a probe is due.
_TICK_S = 60


def is_youtube_only(version):
    """True for a search/snapshot version that can only be fetched from YouTube."""
    if not isinstance(version, dict):
        return False
    src = version.get("source")
    if src == "local":
        return False
    if src == "kn":
        kn = version.get("kn") or {}
        return not (kn.get("divebar") or {}).get("file_id")
    return False


def drop_youtube_only(songs):
    """Grouped search results minus YouTube-only versions (and emptied songs).

    Returns new song dicts; the input is not mutated. ``version_count`` is
    recomputed so the "N versions" label stays honest.
    """
    out = []
    for song in songs or []:
        versions = [v for v in (song.get("versions") or []) if not is_youtube_only(v)]
        if not versions:
            continue
        out.append({**song, "versions": versions, "version_count": len(versions)})
    return out


class YouTubeBreaker:
    def __init__(self, store, config=None, clock=time.time):
        self._store = store
        self._config = config or {}
        self._clock = clock
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._state = self._load()

    # -- persistence -----------------------------------------------------
    def _load(self):
        try:
            raw = self._store._get_meta(META_KEY)
            state = json.loads(raw) if raw else {}
        except Exception:
            state = {}
        return state if isinstance(state, dict) else {}

    def _save(self):
        try:
            self._store._set_meta(META_KEY, json.dumps(self._state))
        except Exception as exc:  # pragma: no cover - best effort
            log_message(f"YouTube breaker: could not persist state: {exc}", self._config)

    @property
    def probe_interval_s(self):
        try:
            return float(self._config.get("youtube_breaker_probe_interval_s",
                                          DEFAULT_PROBE_INTERVAL_S))
        except (TypeError, ValueError):
            return DEFAULT_PROBE_INTERVAL_S

    # -- state -----------------------------------------------------------
    def is_open(self):
        with self._lock:
            return bool(self._state.get("open"))

    def trip(self, reason, url=None):
        """Open the breaker (idempotent). ``url`` becomes the probe target."""
        with self._lock:
            was_open = bool(self._state.get("open"))
            now = self._clock()
            self._state.update(
                open=True,
                reason=(reason or "")[:300],
                probe_url=url or self._state.get("probe_url"),
            )
            if not was_open:
                self._state["opened_at"] = now
                self._state["last_probe_at"] = now
            self._save()
        if not was_open:
            log_message(
                f"YouTube breaker OPEN — hiding YouTube-only versions from singers "
                f"until a download works again (probe every "
                f"{int(self.probe_interval_s // 60)} min): {reason}", self._config)

    def record_success(self):
        """A YouTube download worked → close the breaker."""
        with self._lock:
            if not self._state.get("open"):
                return
            opened_at = self._state.get("opened_at")
            self._state = {"open": False, "closed_at": self._clock()}
            self._save()
        mins = int((self._clock() - opened_at) // 60) if opened_at else "?"
        log_message(f"YouTube breaker CLOSED — YouTube downloads work again "
                    f"(was open {mins} min)", self._config)

    def due_for_probe(self):
        with self._lock:
            if not self._state.get("open") or not self._state.get("probe_url"):
                return False
            last = self._state.get("last_probe_at") or 0
            return self._clock() - last >= self.probe_interval_s

    def status(self):
        with self._lock:
            s = dict(self._state)
        s["open"] = bool(s.get("open"))
        if s["open"]:
            last = s.get("last_probe_at") or self._clock()
            s["next_probe_at"] = last + self.probe_interval_s
        return s

    # -- probe -----------------------------------------------------------
    def probe(self, media):
        """Try one real download of the probe URL; close on success."""
        with self._lock:
            url = self._state.get("probe_url")
            self._state["last_probe_at"] = self._clock()
            self._save()
        if not url:
            return False
        log_message(f"YouTube breaker: probing yt-dlp with {url}", self._config)
        try:
            file_path, _title = media.download_video(url)
        except Exception as exc:
            file_path = None
            log_message(f"YouTube breaker: probe raised {exc}", self._config)
        # A "video unavailable" verdict means yt-dlp reached YouTube fine — only
        # that one video is gone — so it counts as working too (otherwise a
        # probe URL deleted after the trip would keep YouTube off forever).
        reached = sing_resolve.classify_error(
            getattr(media, "_last_error", None)) == sing_resolve.UNAVAILABLE
        if file_path or reached:
            self.record_success()
            return True
        log_message("YouTube breaker: probe failed, YouTube stays off for singers",
                    self._config)
        return False

    def start(self, media):
        if self._thread is not None:
            return

        def _run():
            while not self._stop.wait(_TICK_S):
                try:
                    if self.due_for_probe():
                        self.probe(media)
                except Exception as exc:  # pragma: no cover - never kill the thread
                    log_message(f"YouTube breaker: probe loop error: {exc}", self._config)

        self._thread = threading.Thread(target=_run, name="youtube-breaker", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
