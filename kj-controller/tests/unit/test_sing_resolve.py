"""Tests for sing_resolve — pure decision logic for auto-resolving singer submissions.

The classifier is the risky heart of the fallback feature (deciding whether a
download failure means "this video is gone, try another" vs "a network blip,
retry the same one"), so it gets exhaustive coverage here — no yt-dlp, no
network, no Flask.
"""

import sing_resolve
from sing_resolve import (
    UNAVAILABLE,
    TRANSIENT,
    classify_error,
    next_candidate_index,
)


# --- classify_error: definitively-unavailable -------------------------------

def test_incident_private_video_is_unavailable():
    """The exact 2026-07-09 live-incident error must classify as UNAVAILABLE."""
    msg = (
        "ERROR: [youtube] _vMTtVPhd80: Private video. Sign in if you've been "
        "granted access to this video. Use --cookies-from-browser or --cookies "
        "for the authentication."
    )
    assert classify_error(msg) == UNAVAILABLE


def test_various_unavailable_messages():
    for msg in [
        "ERROR: [youtube] abc: Video unavailable",
        "This video has been removed by the uploader",
        "This video is no longer available because the YouTube account "
        "associated with this video has been terminated.",
        "The uploader has not made this video available in your country",
        "Video unavailable. This video is private",
        "Join this channel to get access to members-only content",
        "Sign in to confirm your age. This video may be inappropriate for some users.",
    ]:
        assert classify_error(msg) == UNAVAILABLE, msg


# --- classify_error: transient ---------------------------------------------

def test_various_transient_messages():
    for msg in [
        "ERROR: Unable to download webpage: The read operation timed out",
        "ERROR: [youtube] abc: HTTP Error 429: Too Many Requests",
        "ERROR: HTTP Error 503: Service Unavailable",
        "WARNING: [youtube] [pot:bgutil:http] Error reaching GET "
        "http://127.0.0.1:4416/ping (caused by TransportError).",
        "Connection reset by peer",
        "[Errno 8] nodename nor servname provided, or not known: getaddrinfo failed",
        "ssl.SSLError: EOF occurred in violation of protocol",
    ]:
        assert classify_error(msg) == TRANSIENT, msg


def test_unknown_and_empty_default_to_transient():
    # Safe default: retrying a good candidate a couple times costs little, but
    # wrongly discarding it on a fluke loses the singer's song.
    assert classify_error("") == TRANSIENT
    assert classify_error(None) == TRANSIENT
    assert classify_error("some totally novel yt-dlp failure mode") == TRANSIENT


def test_classification_is_case_insensitive():
    assert classify_error("PRIVATE VIDEO") == UNAVAILABLE
    assert classify_error("Read Operation TIMED OUT") == TRANSIENT


# --- next_candidate_index ---------------------------------------------------

def test_next_candidate_basic_ordering():
    assert next_candidate_index(total=3, tried=[]) == 0
    assert next_candidate_index(total=3, tried=[0]) == 1
    assert next_candidate_index(total=3, tried=[0, 1]) == 2


def test_next_candidate_skips_tried_out_of_order():
    assert next_candidate_index(total=4, tried=[1]) == 0
    assert next_candidate_index(total=4, tried=[0, 2]) == 1


def test_next_candidate_none_when_exhausted():
    assert next_candidate_index(total=2, tried=[0, 1]) is None
    assert next_candidate_index(total=0, tried=[]) is None


def test_next_candidate_respects_cap():
    # MAX_CANDIDATES distinct attempts is the hard ceiling even if more remain.
    tried = list(range(sing_resolve.MAX_CANDIDATES))
    assert next_candidate_index(total=sing_resolve.MAX_CANDIDATES + 5, tried=tried) is None


def test_caps_are_sane():
    assert sing_resolve.MAX_CANDIDATES >= 1
    assert sing_resolve.MAX_TRANSIENT_RETRIES >= 0
