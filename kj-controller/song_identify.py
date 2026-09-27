"""On-device song identification — "which real song does the singer mean?".

Question 1 of docs/SONG-IDENTIFICATION.md (question 2, "is there a karaoke
version", is ``routes.unified_search``). Works over ``song_id.db`` (built by
``scripts/build_song_id_db.py``): ~2M popular Spotify + karaoke songs.

Pipeline:
  1. normalize the query, drop noise words ("song by", "karaoke", …)
  2. expand each word to plausible spellings via a trigram index over every
     artist/title word (typos within a few edits, prefixes)
  3. retrieve candidates three ways: FTS5 OR of the expansions (bm25);
     phrase matches of word runs against titles ("dark on me", "my tears
     richo*"); and artist-first — any run of words that fuzzily names an
     artist ("the stokes", "sabrina", "bob seg") pulls in that artist's songs
  4. rescore every candidate: how well the query words are explained by the
     candidate's artist+title, how much of the title was typed, fuzzy string
     similarity, popularity and whether a karaoke version exists
  5. decide: ``confident`` (auto-apply), ``candidates`` ("Which one?"), ``none``
     (→ Gemini fallback in gen)
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from text_normalize import normalize

_INITIALS_RE = re.compile(r"\b(?:[a-z0-9] ){1,}[a-z0-9]\b")


def song_norm(text):
    """``text_normalize.normalize`` + join runs of single letters ("u s a" → "usa"),
    so "Party in the U.S.A." and "party in the usa" meet. Used by index and query."""
    return _INITIALS_RE.sub(lambda m: m.group(0).replace(" ", ""), normalize(text or ""))


# Stripped before matching. Only words that are never meaningful in a query — "by",
# "song", "feat" can be real title words ("All By Myself", "Eu Feat. Você"), so
# "song by" is removed only as a phrase.
NOISE_WORDS = {"karaoke", "lyrics", "instrumental", "official"}
NOISE_PHRASE_RE = re.compile(r"\bsong by\b|\bkaraoke version\b")
STOP_WORDS = {"the", "a", "an", "of", "on", "in", "to", "and", "i", "me", "my", "you", "is", "it", "for", "be"}

CANDIDATE_LIMIT = 400
PHRASE_LIMIT = 100
MAX_VARIANTS = 8
ARTIST_SPAN_MAX = 4          # words
ARTIST_MIN_SIM = 0.80        # fuzzy artist-name similarity to count as a hit
ARTISTS_PER_SPAN = 3
ARTIST_LOOKUP_LIMIT = 60
ARTIST_SONG_LIMIT = 1500

# Decision thresholds (tuned on tests/fixtures/song_id_eval.jsonl via scripts/song_id_eval.py).
CONFIDENT_SCORE = 0.80
CONFIDENT_MARGIN = 0.06
CANDIDATE_SCORE = 0.62
# The song must account for what was typed: "that song from titanic" matching a
# Titanic soundtrack title leaves "that song from" unexplained → Gemini, not a guess.
CONFIDENT_MIN_QCOV = 0.80
CANDIDATE_MIN_QCOV = 0.70
# ...and most of the *title* must have been typed (or be a close mangling of it)
# before we auto-apply: stops "cotote joy" being "tidied" to Joywave — Content.
CONFIDENT_MIN_TITLE_COV = 0.80
# Exceptions where a partial title is still safe to auto-apply, both only when the
# artist was typed too: the typed words are the START of the title ("Seal kiss",
# "maximo park boks"), or the whole artist matched and the rest is a close mangling
# of the title ("the stokes max picu" ≈ Machu Picchu).
LEAD_WORD_SIM = 0.75
MANGLED_TITLE_SIM = 0.70

_VERSION_SUFFIX_RE = re.compile(r"\s*[\(\[].*$|\s+-\s+.*$")
# Same title by several artists ("Die Young"): the clearly most popular one is what
# singers mean — confident when it leads the same-title runner-up by this much.
SAME_TITLE_POP_LEAD = 12


@dataclass
class Match:
    artist: str
    title: str
    score: float
    pop: int | None
    karaoke: bool
    detail: dict = field(default_factory=dict)

    def to_dict(self):
        return {"artist": self.artist, "title": self.title, "score": round(self.score, 3),
                "popularity": self.pop, "karaoke": self.karaoke}


def _compact(text):
    return song_norm(text).replace(" ", "")


def _edit_budget(word):
    n = len(word)
    if n <= 3:
        return 1
    if n <= 5:
        return 2
    return 3 if n >= 9 else 2


def _word_sim(q, w):
    """Similarity of a typed word to an index word, 0..1 (prefix typing counts high)."""
    if q == w:
        return 1.0
    if len(q) >= 3 and w.startswith(q):
        return 0.9
    d = Levenshtein.distance(q, w)
    if d > _edit_budget(q):
        return 0.0
    return max(0.0, 1.0 - d / max(len(q), len(w))) * 0.95


class SongIdentifier:
    def __init__(self, db_path):
        self.db_path = db_path
        self._local = threading.local()

    @property
    def available(self):
        return os.path.exists(self.db_path)

    def _db(self):
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
            self._local.db = db
        return db

    # ---- step 2: word expansion
    def _variants(self, word, is_last):
        db = self._db()
        out = {word: 1.0}
        if len(word) >= 3:
            grams = [word[i:i + 3] for i in range(len(word) - 2)]
            match = " OR ".join('"' + g.replace('"', '') + '"' for g in grams)
            rows = db.execute(
                "SELECT v.word, v.freq FROM vocab_tri t JOIN vocab v ON v.word = t.word "
                "WHERE vocab_tri MATCH ? ORDER BY bm25(vocab_tri) LIMIT 300", (match,)).fetchall()
            scored = []
            for w, freq in rows:
                s = _word_sim(word, w)
                if s > 0 and w != word:
                    scored.append((s, freq, w))
            scored.sort(key=lambda x: (-x[0], -x[1]))
            for s, _f, w in scored[:MAX_VARIANTS]:
                out[w] = s
        elif len(word) >= 1:
            # Short words: exact + one-edit neighbours of the same length are too noisy; exact only.
            pass
        return out

    # ---- step 3: candidate retrieval
    _COLS = "s.artist, s.title, s.na, s.nt, s.pop, s.karaoke"

    def _phrase_candidates(self, words):
        """Runs of 2+ consecutive words as a title phrase (last word may be partial)."""
        db = self._db()
        out = []
        n = len(words)
        for i in range(n):
            for j in range(n, i + 1, -1):
                phrase = " ".join(w.replace('"', "") for w in words[i:j])
                q = f'nt : "{phrase}"' + (" *" if j == n else "")
                out += db.execute(
                    f"SELECT {self._COLS} FROM songs_fts f JOIN songs s ON s.rowid = f.rowid "
                    "WHERE songs_fts MATCH ? ORDER BY s.pop DESC LIMIT ?", (q, PHRASE_LIMIT)).fetchall()
                break   # longest run starting at i is enough
        return out

    def _artist_candidates(self, words, variants):
        """Any run of words that fuzzily names an artist → that artist's songs.

        Each word of the run (its spelling variants, or as a prefix) must occur in
        the artist's name ("the stokes"→The Strokes, "bob seg"→Bob Seger,
        "sabrina"→Sabrina Carpenter), then the run must be string-similar to it."""
        db = self._db()
        hits = {}
        n = len(words)
        for i in range(n):
            for j in range(i + 1, min(n, i + ARTIST_SPAN_MAX) + 1):
                span_words = words[i:j]
                span = " ".join(span_words)
                if len(span.replace(" ", "")) < 4 or all(w in STOP_WORDS for w in span_words):
                    continue
                clauses = []
                for w in span_words:
                    alts = ['"' + v.replace('"', "") + '"' for v in variants[w]]
                    if len(w) >= 3:
                        alts.append('"' + w.replace('"', "") + '"*')
                    clauses.append("(" + " OR ".join(alts) + ")")
                rows = db.execute(
                    "SELECT a.na, a.pop FROM artists_fts f JOIN artists a ON a.rowid = f.rowid "
                    "WHERE artists_fts MATCH ? ORDER BY a.pop DESC LIMIT ?",
                    (" AND ".join(clauses), ARTIST_LOOKUP_LIMIT)).fetchall()
                scored = []
                for na, pop in rows:
                    sim = max(fuzz.ratio(span, na), fuzz.partial_ratio(span, na) if len(span) >= 5 else 0) / 100
                    if sim >= ARTIST_MIN_SIM:
                        scored.append((sim + (pop or 0) / 400, na))
                for _s, na in sorted(scored, reverse=True)[:ARTISTS_PER_SPAN]:
                    hits[na] = True
        out = []
        for na in hits:
            out += db.execute(f"SELECT {self._COLS} FROM songs s WHERE s.na = ? ORDER BY s.pop DESC LIMIT ?",
                              (na, ARTIST_SONG_LIMIT)).fetchall()
        return out

    def _candidates(self, words, variants):
        db = self._db()
        terms = []
        for w in words:
            if w in STOP_WORDS and len(words) > 1:
                continue
            for v in variants[w]:
                terms.append('"' + v.replace('"', '') + '"')
        if words and len(words[-1]) >= 2:
            terms.append('"' + words[-1].replace('"', '') + '"*')
        if not terms:
            return []
        q = " OR ".join(dict.fromkeys(terms))
        return db.execute(
            "SELECT s.artist, s.title, s.na, s.nt, s.pop, s.karaoke FROM songs_fts f "
            "JOIN songs s ON s.rowid = f.rowid WHERE songs_fts MATCH ? "
            "ORDER BY bm25(songs_fts) LIMIT ?", (q, CANDIDATE_LIMIT)).fetchall()

    # ---- step 4: rescoring
    @staticmethod
    def _score(words, qnorm, na, nt, pop, karaoke):
        """→ (score, detail). 0 when nothing of the title was typed."""
        a_toks, t_toks = na.split(), nt.split()
        hay = a_toks + t_toks
        compact_hay = na.replace(" ", "") + nt.replace(" ", "")
        used = set()
        hay_sim = {}
        explained = 0.0
        weight = 0.0
        for w in words:
            wt = 0.4 if w in STOP_WORDS else min(1.0, 0.4 + 0.15 * len(w))
            weight += wt
            best, best_i = 0.0, None
            for i, h in enumerate(hay):
                s = _word_sim(w, h)
                if i in used:
                    s *= 0.5    # each title/artist word explains one typed word ("big big plans")
                if s > best:
                    best, best_i = s, i
            if best < 0.5 and len(w) >= 3 and w in compact_hay:
                best = 0.8
            if best_i is not None:
                used.add(best_i)
                hay_sim[best_i] = max(hay_sim.get(best_i, 0.0), best)
            explained += wt * best
        q_cov = explained / weight if weight else 0.0

        def covered(toks, offset):
            sig = [i for i, t in enumerate(toks) if t not in STOP_WORDS] or list(range(len(toks)))
            return sum(1 for i in sig if (i + offset) in used) / len(sig) if sig else 0.0
        title_cov = covered(t_toks, len(a_toks))
        artist_cov = covered(a_toks, 0)
        # What's left after the artist words: how close is it to the title as a string?
        # Catches mangled titles word-matching misses ("max picu" ≈ "machu picchu").
        artist_hit = {i for i in used if i < len(a_toks)}
        leftover = " ".join(w for w in words if not any(
            _word_sim(w, a_toks[i]) >= 0.5 for i in artist_hit)) or qnorm
        title_sim = max(fuzz.ratio(leftover, nt), fuzz.ratio(leftover.replace(" ", ""), nt.replace(" ", ""))) / 100
        if title_cov == 0 and title_sim < 0.6:
            return 0.0, {}
        if title_sim >= 0.6 and artist_cov > 0:
            # the leftover words ARE the title, just mangled — count them as explained
            q_cov = max(q_cov, (artist_cov * len(artist_hit) + title_sim * len(leftover.split()))
                        / max(1, len(artist_hit) + len(leftover.split())))
            title_cov = max(title_cov, title_sim)
        # How many of the title's leading words were typed (stop words may be skipped)?
        title_lead = 0
        for k, tok in enumerate(t_toks):
            if hay_sim.get(len(a_toks) + k, 0.0) >= LEAD_WORD_SIM:
                title_lead += 1
            elif tok not in STOP_WORDS:
                break
        joined = f"{na} {nt}"
        sim = max(fuzz.token_set_ratio(qnorm, joined), fuzz.WRatio(qnorm, joined)) / 100
        text = 0.45 * q_cov + 0.20 * title_cov + 0.08 * artist_cov + 0.12 * sim + 0.15 * title_sim
        prior = (pop if pop is not None else 30) / 100
        score = text + 0.10 * prior + (0.05 if karaoke else 0.0)
        if leftover.replace(" ", "") == nt.replace(" ", ""):
            score += 0.06   # typed the exact title: "moon" is MOON, not the more popular "Moonlight"
        return score, {"q_cov": round(q_cov, 2), "title_cov": round(title_cov, 2),
                       "artist_cov": round(artist_cov, 2), "sim": round(sim, 2),
                       "title_sim": round(title_sim, 2), "title_lead": title_lead}

    def identify(self, query, limit=5):
        """→ {status: confident|candidates|none, best, candidates[]}."""
        qnorm = NOISE_PHRASE_RE.sub(" ", song_norm(query or ""))
        words = [w for w in qnorm.split() if w not in NOISE_WORDS]
        if not words or len("".join(words)) < 3:
            return {"status": "none", "best": None, "candidates": []}
        qnorm = " ".join(words)
        variants = {w: self._variants(w, i == len(words) - 1) for i, w in enumerate(words)}
        seen = {}
        pool = self._candidates(words, variants) + self._phrase_candidates(words) + self._artist_candidates(words, variants)
        for artist, title, na, nt, pop, karaoke in pool:
            s, detail = self._score(words, qnorm, na, nt, pop, karaoke)
            if s <= 0:
                continue
            # One entry per artist + base title: "Feliz Navidad" and "Feliz Navidad -
            # Remastered 2006" are the same song for identification purposes.
            key = (na, _compact(_VERSION_SUFFIX_RE.sub("", title)) or nt)
            if key not in seen or s > seen[key].score:
                seen[key] = Match(artist, title, s, pop, bool(karaoke), detail)
        ranked = sorted(seen.values(), key=lambda m: -m.score)
        if not ranked:
            return {"status": "none", "best": None, "candidates": []}
        best = ranked[0]
        runner = ranked[1] if len(ranked) > 1 else None
        margin = best.score - (runner.score if runner else 0.0)
        if runner and _compact(runner.title) == _compact(best.title):
            # Same title, other artist: popularity decides ("Die Young" → Kesha).
            lead = (best.pop or 30) - (runner.pop or 30) + (10 if best.karaoke and not runner.karaoke else 0)
            if lead >= SAME_TITLE_POP_LEAD:
                margin = max(margin, CONFIDENT_MARGIN)
        top = [m.to_dict() for m in ranked[:limit]]
        d = best.detail
        qcov = d.get("q_cov", 0)
        title_ok = (d.get("title_cov", 0) >= CONFIDENT_MIN_TITLE_COV
                    or (d.get("artist_cov", 0) > 0 and d.get("title_lead", 0) >= 1)
                    or (d.get("artist_cov", 0) >= 0.99 and d.get("title_sim", 0) >= MANGLED_TITLE_SIM))
        if (best.score >= CONFIDENT_SCORE and margin >= CONFIDENT_MARGIN
                and qcov >= CONFIDENT_MIN_QCOV and title_ok):
            status = "confident"
        elif best.score >= CANDIDATE_SCORE and qcov >= CANDIDATE_MIN_QCOV:
            status = "candidates"
        else:
            status = "none"
        return {"status": status, "best": best.to_dict() | {"detail": best.detail}, "candidates": top}
