"""Decide which folders get the full oracle vs a stratified audit.

full-oracle = the pre-marker messy era (method name-match / leftover*) where
filenames are unreliable. audit = a random, stratified sample of the trusted
marker eras so every era the user described is spot-checked; escalate a whole
era into the full oracle if the audit finds a bad pick there.
"""
from __future__ import annotations
import random

_FULL_METHODS = {"name-match", "leftover-only", "leftover-ambiguous"}


def full_oracle_brands(rows: list[dict]) -> list[str]:
    return sorted(r["brand_code"] for r in rows if r["method"] in _FULL_METHODS)


def _label(method: str) -> str:
    # method is a '+'-joined marker label list; first token is enough to bucket.
    return (method or "").split("+")[0]


def audit_sample(rows: list[dict], per_label: int = 7, uploaded_all: bool = True,
                 no_source_n: int = 8, seed: int = 1729) -> list[str]:
    rng = random.Random(seed)
    by_label: dict[str, list[str]] = {}
    for r in rows:
        if r["method"] in _FULL_METHODS:
            continue                         # never audit the full-oracle zone
        by_label.setdefault(_label(r["method"]), []).append(r["brand_code"])

    picked: list[str] = []
    for label, brands in by_label.items():
        brands = sorted(brands)
        if label == "uploaded" and uploaded_all:
            n = len(brands)
        elif label == "karaoke-sourced":
            n = min(no_source_n, len(brands))
        else:
            n = min(per_label, len(brands))
        picked.extend(rng.sample(brands, n))
    return sorted(picked)
