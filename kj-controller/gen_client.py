"""GenClient: HTTP client for the gen API (karaoke video generation)."""

import logging

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30
# create_job runs gen's audio search inside the request (flacfetch + YouTube
# fan-out), which can take well over 30s. Timing out client-side does NOT stop
# the job gen already created, so a too-short timeout orphans real jobs.
CREATE_JOB_TIMEOUT = 120


class GenStatus:
    """Mapped gen status values stored in rotation entries."""
    PROCESSING = "processing"
    # Gen needs a human before it can continue (pick audio, trim audio, confirm
    # a long duration) — distinct from lyrics review, which has its own page.
    NEEDS_INPUT = "needs_input"
    AWAITING_REVIEW = "awaiting_review"
    # Lyrics review opened (gen ``in_review``) by the singer / unknown, or by
    # the KJ ("host_review" — gen's state_data.review_started_by == "admin").
    # The singer page says "the host has already started reviewing".
    IN_REVIEW = "in_review"
    HOST_REVIEW = "host_review"
    RENDERING = "rendering"
    # kjbox-side: gen finished; waiting for master-sync to pull the NOMAD-####
    # master onto the box so it can be linked (see GenPoller).
    SYNCING = "syncing"
    COMPLETE = "complete"
    FAILED = "failed"

    TERMINAL = {COMPLETE, FAILED}
    ACTIVE = {PROCESSING, NEEDS_INPUT, AWAITING_REVIEW, IN_REVIEW, HOST_REVIEW,
              RENDERING, SYNCING}


_STATUS_MAP = {
    "pending": GenStatus.PROCESSING,
    "queued": GenStatus.PROCESSING,
    "processing": GenStatus.PROCESSING,
    "searching_audio": GenStatus.PROCESSING,
    "downloading": GenStatus.PROCESSING,
    "downloading_audio": GenStatus.PROCESSING,
    "download_pending_retry": GenStatus.PROCESSING,
    "audio_edit_complete": GenStatus.PROCESSING,
    "audio_complete": GenStatus.PROCESSING,
    "separating_stage1": GenStatus.PROCESSING,
    "separating_stage2": GenStatus.PROCESSING,
    "transcribing": GenStatus.PROCESSING,
    "correcting": GenStatus.PROCESSING,
    "lyrics_complete": GenStatus.PROCESSING,
    "generating_screens": GenStatus.PROCESSING,
    "applying_padding": GenStatus.PROCESSING,
    "awaiting_audio_selection": GenStatus.NEEDS_INPUT,
    "awaiting_audio_edit": GenStatus.NEEDS_INPUT,
    "in_audio_edit": GenStatus.NEEDS_INPUT,
    "awaiting_duration_confirm": GenStatus.NEEDS_INPUT,
    "awaiting_instrumental_selection": GenStatus.NEEDS_INPUT,
    "awaiting_review": GenStatus.AWAITING_REVIEW,
    "in_review": GenStatus.IN_REVIEW,
    "review_complete": GenStatus.RENDERING,
    "rendering_video": GenStatus.RENDERING,
    "render_pending_capacity": GenStatus.RENDERING,
    "generating_video": GenStatus.RENDERING,
    "instrumental_selected": GenStatus.RENDERING,
    "encoding": GenStatus.RENDERING,
    "packaging": GenStatus.RENDERING,
    "uploading": GenStatus.RENDERING,
    "notifying": GenStatus.RENDERING,
    "ready_for_finalization": GenStatus.RENDERING,
    "finalizing": GenStatus.RENDERING,
    "complete": GenStatus.COMPLETE,
    "prep_complete": GenStatus.COMPLETE,
    "failed": GenStatus.FAILED,
    "cancelled": GenStatus.FAILED,
    "error": GenStatus.FAILED,
}


class GenApiError(Exception):
    """A gen API call failed. ``status`` is the HTTP status (0 = no response),
    ``detail`` gen's error detail (e.g. "invalid_code", "signup_cap")."""

    def __init__(self, status, detail=""):
        super().__init__(f"gen API {status}: {detail}")
        self.status = status
        self.detail = detail


def map_gen_status(api_status):
    """Map a gen API job status string to a rotation display status."""
    return _STATUS_MAP.get(api_status, GenStatus.PROCESSING)


def map_gen_job(job_data):
    """Like ``map_gen_status`` but from the full job, so an in-progress review
    the KJ opened (``state_data.review_started_by == "admin"``) is told apart."""
    job_data = job_data or {}
    status = map_gen_status(job_data.get("status", ""))
    if status == GenStatus.IN_REVIEW:
        started_by = (job_data.get("state_data") or {}).get("review_started_by")
        if started_by == "admin":
            return GenStatus.HOST_REVIEW
    return status


class GenClient:
    """HTTP client for the gen API.

    Two credentials: the admin ``token`` (KJ-side calls, job polling) and the
    ``kjbox_secret`` partner secret for the singer make-it flow, whose calls run
    AS the singer's own gen account (their session token) so the job, the
    delivery email and the customer relationship are theirs.
    """

    # Attribution: gen records X-Client-Id into the job's request_metadata and
    # sends kjbox jobs' review emails with a one-click sign-in link.
    CLIENT_ID = "kjbox"
    # gen's audio search fans out to flacfetch/YouTube/Spotify (~15-30s).
    SEARCH_TIMEOUT = 60

    def __init__(self, api_url, token, kjbox_secret=""):
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.kjbox_secret = kjbox_secret or ""

    def _headers(self):
        return {"X-Admin-Token": self.token, "Content-Type": "application/json"}

    def create_job(self, artist, title):
        """Create a gen job via audio search with auto_download.

        Returns dict with job_id and status.
        """
        resp = requests.post(
            f"{self.api_url}/api/audio-search/search",
            json={"artist": artist, "title": title, "auto_download": True, "theme_id": "nomad"},
            headers=self._headers(),
            timeout=CREATE_JOB_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def get_job_status(self, job_id):
        """Poll job status. Returns dict with status, state_data, file_urls."""
        resp = requests.get(
            f"{self.api_url}/api/jobs/{job_id}",
            headers=self._headers(),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    PARSE_TIMEOUT = 60

    def parse_titles(self, items):
        """Parse a batch of karaoke filenames -> [{id, artist, title, confidence}].

        Returns the results list on success, or None on ANY failure (offline,
        timeout, missing/undeployed endpoint, bad status) so the caller keeps
        its deterministic guess. Empty input short-circuits to [].
        """
        if not items:
            return []
        try:
            resp = requests.post(
                f"{self.api_url}/api/parse-karaoke-titles",
                json={"items": items},
                headers=self._headers(),
                timeout=self.PARSE_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results")
            return results if isinstance(results, list) else None
        except Exception as e:
            logger.warning("parse_titles failed (offline?): %s", e)
            return None

    def get_download_url(self, job_id, quality="lossy_720p_mp4"):
        """Get download URL for a completed job.

        Returns full URL string for streaming download, or None if not available.
        """
        try:
            resp = requests.get(
                f"{self.api_url}/api/jobs/{job_id}/download-urls",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            finals = data.get("download_urls", {}).get("finals", {})
            relative_url = finals.get(quality)
            if relative_url:
                return f"{self.api_url}{relative_url}?token={self.token}"
            return None
        except Exception as e:
            logger.error("Failed to get download URL for job %s: %s", job_id, e)
            return None

    # ------------------------------------------------------------------
    # Singer make-it flow (partner secret + the singer's gen session)
    # ------------------------------------------------------------------

    def _singer_headers(self, path, session_token=None, locale=None):
        headers = {"Content-Type": "application/json", "X-Client-Id": self.CLIENT_ID}
        # The partner secret goes ONLY to gen's partner endpoints — gen records
        # custom request headers on jobs, which the singer can read back.
        if self.kjbox_secret and path.startswith("/api/kjbox/"):
            headers["X-Kjbox-Secret"] = self.kjbox_secret
        if session_token:
            headers["Authorization"] = f"Bearer {session_token}"
        if locale:
            headers["Accept-Language"] = locale
        return headers

    def _singer_call(self, method, path, *, session_token=None, locale=None,
                     json=None, timeout=REQUEST_TIMEOUT):
        try:
            resp = requests.request(
                method, f"{self.api_url}{path}", json=json, timeout=timeout,
                headers=self._singer_headers(path, session_token, locale),
            )
        except requests.RequestException as exc:
            raise GenApiError(0, str(exc)) from exc
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else ""
            if isinstance(detail, dict):   # gen's 402 carries a dict detail
                detail = detail.get("message") or detail.get("detail") or "error"
            raise GenApiError(resp.status_code, detail or resp.reason)
        return data

    def singer_flow_configured(self):
        return bool(self.kjbox_secret)

    def send_login_code(self, email, locale=None, venue=None):
        return self._singer_call("POST", "/api/kjbox/auth/send-code", locale=locale,
                                 json={"email": email, "locale": locale, "venue": venue})

    def verify_login_code(self, email, code, locale=None):
        return self._singer_call("POST", "/api/kjbox/auth/verify-code", locale=locale,
                                 json={"email": email, "code": code})

    def grant_show_credit(self, session_token, idempotency_key, venue=None, only_if_empty=False):
        """+1 free "show" credit. ``only_if_empty`` grants only at a 0 balance;
        gen claims ``idempotency_key`` only when it actually grants, so one key
        yields at most one credit across the search-time and submit-time calls."""
        return self._singer_call("POST", "/api/kjbox/credits/show-credit",
                                 session_token=session_token,
                                 json={"idempotency_key": idempotency_key, "venue": venue,
                                       "only_if_empty": only_if_empty})

    def match_judge(self, session_token, artist, title, stage="fast", audio_confidence_tier=None):
        body = {"artist": artist, "title": title, "stage": stage}
        if audio_confidence_tier:
            body["audio_confidence_tier"] = audio_confidence_tier
        return self._singer_call("POST", "/api/catalog/match-judge",
                                 session_token=session_token, json=body)

    def search_audio(self, session_token, artist, title):
        return self._singer_call("POST", "/api/audio-search/search-standalone",
                                 session_token=session_token, timeout=self.SEARCH_TIMEOUT,
                                 json={"artist": artist, "title": title})

    def validate_url(self, session_token, url):
        return self._singer_call("POST", "/api/jobs/validate-url",
                                 session_token=session_token, json={"url": url})

    def create_job_from_search(self, session_token, search_session_id, selection_index,
                               artist, title):
        """Public, default-branded job — no audio edit, gen's automatic review."""
        return self._singer_call("POST", "/api/jobs/create-from-search",
                                 session_token=session_token, timeout=CREATE_JOB_TIMEOUT,
                                 json={"search_session_id": search_session_id,
                                       "selection_index": selection_index,
                                       "artist": artist, "title": title,
                                       "is_private": False, "requires_audio_edit": False,
                                       "review_mode": "auto", "backing_preference": "auto"})

    def create_job_from_url(self, session_token, url, artist, title):
        return self._singer_call("POST", "/api/jobs/create-from-url",
                                 session_token=session_token, timeout=CREATE_JOB_TIMEOUT,
                                 json={"url": url, "artist": artist, "title": title,
                                       "is_private": False, "review_mode": "auto"})

    def review_link(self, session_token, job_id, locale=None):
        """One-click sign-in link to the singer's own job's lyrics review.

        → {url, status, review_started_by}; 409 ``not_in_review`` once the
        review is over."""
        return self._singer_call("POST", f"/api/kjbox/jobs/{job_id}/review-link",
                                 session_token=session_token, locale=locale,
                                 json={"locale": locale})
