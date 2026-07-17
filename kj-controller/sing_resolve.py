"""Pure decision logic for auto-resolving singer submissions.

When a singer's picked YouTube version fails to download, the download worker
consults this module to decide what to do next:

- ``classify_error`` — was the failure because the video is *gone*
  (private/deleted/blocked → try a different candidate) or a *transient* blip
  (timeout/429/network → retry the same candidate)?
- ``next_candidate_index`` — which version to try next, bounded by
  ``MAX_CANDIDATES`` so resolution stays fast during a live show.

Deliberately dependency-free (no yt-dlp, no network, no Flask) so the risky
classification logic is exhaustively unit-testable. See
``docs/archive/2026-07-09-singer-submission-validation-design.md``.
"""

# Try at most this many *distinct* candidate versions before giving up and
# flagging the entry for the KJ. Keeps auto-resolution bounded during a show.
MAX_CANDIDATES = 3

# Per-candidate transient retries before advancing to the next candidate. A
# transient error never terminates resolution on its own — it retries this many
# times, then falls through to the next candidate.
MAX_TRANSIENT_RETRIES = 2

# Classification outcomes.
UNAVAILABLE = "unavailable"  # video is gone → advance to the next candidate
TRANSIENT = "transient"      # network/service blip → retry the same candidate


# Substrings (matched case-insensitively) that mean the video itself cannot be
# fetched by anyone with our credentials — retrying is pointless, move on.
_UNAVAILABLE_PATTERNS = (
    "private video",
    "video is private",
    "video unavailable",
    "video is unavailable",
    "has been removed",
    "removed by the uploader",
    "removed for violating",
    "no longer available",
    "account associated with this video has been terminated",
    "account has been terminated",
    "not made this video available in your country",
    "blocked it in your country",
    "not available in your country",
    "members-only",
    "join this channel",
    "confirm your age",
    "video does not exist",
    "does not exist",
)

# Substrings that mean "try again" — the request never reached a verdict about
# the video, so the same candidate deserves another shot.
_TRANSIENT_PATTERNS = (
    "timed out",
    "timeout",
    "temporarily",
    "try again later",
    "http error 5",  # 500 / 502 / 503 ...
    "429",
    "too many requests",
    "bgutil",
    "pot provider",
    "getaddrinfo",
    "network is unreachable",
    "connection reset",
    "connection refused",
    "connection aborted",
    "eof occurred",
    "unable to download webpage",
    "failed to resolve",
    "ssl",
)


def classify_error(message):
    """Classify a download failure ``message`` as ``UNAVAILABLE`` or ``TRANSIENT``.

    Unknown or empty messages default to ``TRANSIENT``: retrying a still-good
    candidate a couple of times is cheap, whereas wrongly discarding it on a
    fluke loses the singer's song. ``UNAVAILABLE`` patterns are checked first
    since they are the more specific signal.
    """
    text = (message or "").lower()
    for pattern in _UNAVAILABLE_PATTERNS:
        if pattern in text:
            return UNAVAILABLE
    for pattern in _TRANSIENT_PATTERNS:
        if pattern in text:
            return TRANSIENT
    return TRANSIENT


def next_candidate_index(total, tried):
    """Return the next untried candidate index, or ``None`` when resolution stops.

    Stops (returns ``None``) when every candidate has been tried, when there are
    no candidates, or when the ``MAX_CANDIDATES`` distinct-attempt cap is hit.
    ``tried`` is the collection of indices already attempted.
    """
    tried_set = set(tried)
    if len(tried_set) >= MAX_CANDIDATES:
        return None
    for i in range(total):
        if i not in tried_set:
            return i
    return None
