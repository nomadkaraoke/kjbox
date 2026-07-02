"""GenClient: HTTP client for the gen API (karaoke video generation)."""

import logging

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


class GenStatus:
    """Mapped gen status values stored in rotation entries."""
    PROCESSING = "processing"
    AWAITING_REVIEW = "awaiting_review"
    RENDERING = "rendering"
    COMPLETE = "complete"
    FAILED = "failed"

    TERMINAL = {COMPLETE, FAILED}
    ACTIVE = {PROCESSING, AWAITING_REVIEW, RENDERING}


_STATUS_MAP = {
    "pending": GenStatus.PROCESSING,
    "downloading": GenStatus.PROCESSING,
    "separating_stage1": GenStatus.PROCESSING,
    "separating_stage2": GenStatus.PROCESSING,
    "transcribing": GenStatus.PROCESSING,
    "generating_screens": GenStatus.PROCESSING,
    "awaiting_review": GenStatus.AWAITING_REVIEW,
    "in_review": GenStatus.AWAITING_REVIEW,
    "review_complete": GenStatus.RENDERING,
    "rendering_video": GenStatus.RENDERING,
    "generating_video": GenStatus.RENDERING,
    "instrumental_selected": GenStatus.RENDERING,
    "complete": GenStatus.COMPLETE,
    "failed": GenStatus.FAILED,
}


def map_gen_status(api_status):
    """Map a gen API job status string to a rotation display status."""
    return _STATUS_MAP.get(api_status, GenStatus.PROCESSING)


class GenClient:
    """HTTP client for the gen API."""

    def __init__(self, api_url, token):
        self.api_url = api_url.rstrip("/")
        self.token = token

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
            timeout=REQUEST_TIMEOUT,
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
