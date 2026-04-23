"""Unit tests for VAPID key bootstrap logic."""

import json
import os

import pytest


def test_bootstrap_generates_keys_when_missing(tmp_path):
    from app import _bootstrap_vapid_keys
    cfg_path = tmp_path / "config.json"
    cfg = {"vapid_subject": "mailto:test@example.com"}
    cfg_path.write_text(json.dumps(cfg))
    _bootstrap_vapid_keys(cfg, str(cfg_path))
    assert cfg["vapid_public_key"]
    assert cfg["vapid_private_key"]
    # Persisted to disk
    saved = json.loads(cfg_path.read_text())
    assert saved["vapid_public_key"] == cfg["vapid_public_key"]
    assert saved["vapid_private_key"] == cfg["vapid_private_key"]


def test_bootstrap_preserves_existing_keys(tmp_path):
    from app import _bootstrap_vapid_keys
    cfg_path = tmp_path / "config.json"
    cfg = {
        "vapid_subject": "mailto:test@example.com",
        "vapid_public_key": "PRESERVED_PUBLIC",
        "vapid_private_key": "PRESERVED_PRIVATE",
    }
    cfg_path.write_text(json.dumps(cfg))
    _bootstrap_vapid_keys(cfg, str(cfg_path))
    assert cfg["vapid_public_key"] == "PRESERVED_PUBLIC"
    assert cfg["vapid_private_key"] == "PRESERVED_PRIVATE"


def test_bootstrap_tolerates_unwritable_config(tmp_path, caplog):
    import logging
    from app import _bootstrap_vapid_keys
    cfg = {"vapid_subject": "mailto:test@example.com"}
    # Non-existent path — write will fail
    with caplog.at_level(logging.ERROR):
        _bootstrap_vapid_keys(cfg, "/nonexistent/path/config.json")
    # Keys still generated in-memory
    assert cfg["vapid_public_key"]
    assert cfg["vapid_private_key"]
    # Error logged mentioning VAPID
    assert any("VAPID" in rec.message for rec in caplog.records)


def test_bootstrap_sets_default_subject_when_missing(tmp_path):
    from app import _bootstrap_vapid_keys
    cfg_path = tmp_path / "config.json"
    cfg = {}  # No vapid_subject
    cfg_path.write_text(json.dumps(cfg))
    _bootstrap_vapid_keys(cfg, str(cfg_path))
    # Default subject should be set
    assert cfg.get("vapid_subject", "").startswith("mailto:")


def test_bootstrap_generates_b64url_keys(tmp_path):
    """Public key should be base64url encoded and decode to a 65-byte P-256 point."""
    import base64
    from app import _bootstrap_vapid_keys
    cfg_path = tmp_path / "config.json"
    cfg = {}
    cfg_path.write_text(json.dumps(cfg))
    _bootstrap_vapid_keys(cfg, str(cfg_path))
    pub = cfg["vapid_public_key"]
    # Pad back up to a multiple of 4 for b64 decoding
    padded = pub + "=" * (-len(pub) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    # Uncompressed P-256 point = 0x04 prefix + 32 bytes X + 32 bytes Y = 65 bytes
    assert len(raw) == 65
    assert raw[0] == 0x04
