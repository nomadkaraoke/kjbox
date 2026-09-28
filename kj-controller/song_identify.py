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

from functools import lru_cache

from rapidfuzz import fuzz, process
from rapidfuzz.distance import Levenshtein

from text_normalize import NORMALIZER_VERSION, normalize  # noqa: F401 (re-exported for the sync)

_INITIALS_RE = re.compile(r"\b(?:[a-z0-9] ){1,}[a-z0-9]\b")


def song_norm(text):
    """``text_normalize.normalize`` + join runs of single letters ("u s a" → "usa"),
    so "Party in the U.S.A." and "party in the usa" meet. Used by index and query."""
    return _INITIALS_RE.sub(lambda m: m.group(0).replace(" ", ""), normalize(text or ""))


# Stripped before matching. Only words that are never meaningful in a query — "by",
# "song", "feat" can be real title words ("All By Myself", "Eu Feat. Você"), so
# "song by" is removed only as a phrase.
NOISE_WORDS = {"karaoke", "lyrics", "instrumental", "official"}
# In the INDEX, "Song (feat. X)" normalizes to "song" (text_normalize strips the
# feat clause). In a QUERY the singer's words after "feat." are real ("drake feat
# rihanna what's my name"), so only the feat word itself is dropped.
_QUERY_FEAT_RE = re.compile(r"\b(?:featuring|feat|ft)\b\.?", re.IGNORECASE)
# The featured artist(s) a singer typed ("see you again (feat. kali uchis)"): the
# index drops "(feat. X)" from titles, so these words are OPTIONAL when scoring.
_QUERY_FEAT_CLAUSE_RE = re.compile(r"\b(?:featuring|feat|ft)\b\.?\s+([^)\]]*)", re.IGNORECASE)
FEAT_WORD_WEIGHT = 0.1
NOISE_PHRASE_RE = re.compile(r"\bsong by\b|\bkaraoke version\b")
STOP_WORDS = {"the", "a", "an", "of", "on", "in", "to", "and", "i", "me", "my", "you", "is", "it", "for", "be"}

CANDIDATE_LIMIT = 150
CANDIDATE_LOO_MAX = 6        # leave-one-word-out queries
DEDUPE_TOP = 30
NEAR_DUP_TITLE_RATIO = 92    # same artist + this similar a title = the same song (MB misspellings)
LOO_SKIP_IF = 40             # skip leave-one-out when the all-words query found this many              # credit-variant collapsing looks at the best N only
PHRASE_LIMIT = 100
MAX_VARIANTS = 8
ARTIST_SPAN_MAX = 4          # words
ARTIST_MIN_SIM = 0.80        # fuzzy artist-name similarity to count as a hit
ARTISTS_PER_SPAN = 3
ARTIST_LOOKUP_LIMIT = 60
ARTIST_SONG_LIMIT = 1500
PRESCORE_KEEP = 200          # C-speed rapidfuzz pre-filter before the detailed Python scoring

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
GENERIC_TITLE_ARTISTS = 3    # title-only query + this many same-title songs → stricter lead
GENERIC_TITLE_POP_LEAD = 25


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


def _prefix_ok(word):
    """Treat a word as partially typed (FTS prefix query) only if it's long enough
    to be selective: "me*"/"on*" expand to thousands of index terms."""
    return len(word) >= 4 and word not in STOP_WORDS


def _compact(text):
    return song_norm(text).replace(" ", "")


_CREDIT_SPLIT_RE = re.compile(r"\s*(?:,|&|\+|/|\bfeaturing\b|\bfeat\.?|\bft\.?|\bx\b|\band\b|\bwith\b)\s*",
                              re.IGNORECASE)


def _credits(artist):
    return {c for c in (_compact(p) for p in _CREDIT_SPLIT_RE.split(artist or "")) if c}


def _same_song(a, b):
    """Same base title — or a near-identical spelling of it by the same artist, as
    MusicBrainz duplicates often are ("Machu Picchu" / "Machu Piccu") — and a
    credited artist in common. "Hayloft" vs "Hayloft II" (~82%) stay distinct."""
    if not (_credits(a.artist) & _credits(b.artist)):
        return False
    ta = _compact(_VERSION_SUFFIX_RE.sub("", a.title))
    tb = _compact(_VERSION_SUFFIX_RE.sub("", b.title))
    if ta == tb:
        return True
    if re.sub(r"\D", "", ta) != re.sub(r"\D", "", tb):
        return False    # numbers differ: a sequel/part, not a misspelling ("Hayloft" vs "Hayloft II")
    return min(len(ta), len(tb)) >= 6 and fuzz.ratio(ta, tb) >= NEAR_DUP_TITLE_RATIO


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
    if len(q) >= 4 and len(w) > len(q) and Levenshtein.distance(q, w[:len(q)]) <= 1:
        return 0.75     # a mistyped start of the word ("richo" → "ricochet")
    d = Levenshtein.distance(q, w)
    if d > _edit_budget(q):
        return 0.0
    return max(0.0, 1.0 - d / max(len(q), len(w))) * 0.95


def default_db_path(config):
    """``song_id_db`` config key, else next to the app (like catalog_mirror.db)."""
    return (config or {}).get("song_id_db") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "song_id.db")


class SongIdentifier:
    def __init__(self, db_path):
        self.db_path = db_path
        self._local = threading.local()
        self._generation = 0
        self._buckets, self._bucket_gen = {}, 0

    @property
    def available(self):
        return os.path.exists(self.db_path)

    def reload(self):
        """Reopen after nomad-catalog-sync atomically replaced the file (open
        connections would keep reading the old, unlinked inode)."""
        self._generation += 1

    def _db(self):
        db, gen = getattr(self._local, "db", None), getattr(self._local, "gen", -1)
        if db is None or gen != self._generation:
            if db is not None:
                db.close()
            db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
            self._local.db, self._local.gen = db, self._generation
        return db

    def stats(self):
        out = {"db_path": self.db_path, "available": self.available}
        if self.available:
            try:
                out.update(dict(self._db().execute("SELECT key, value FROM meta").fetchall()))
            except sqlite3.Error as exc:
                out["error"] = str(exc)
        return out

    # ---- step 2: word expansion
    def _variants(self, word, is_last=False):
        """Index words the typed ``word`` might be a typo of → {word: similarity}.

        Candidates share the first letter (typos rarely hit it: "rihana", "cheery",
        "balck") and are within the edit budget in length; rapidfuzz scores the
        few thousand in C. Cached per index generation (typing repeats words)."""
        return dict(self._variants_cached(word, self._generation))

    def _vocab_bucket(self, first, length):
        """{word: freq} for one (first letter, length) bucket, cached in memory per
        index generation — fetching ~20K rows from SQLite per lookup dominated."""
        key = (first, length)
        if self._bucket_gen != self._generation:
            self._buckets, self._bucket_gen = {}, self._generation
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = dict(self._db().execute(
                "SELECT word, freq FROM vocab WHERE first = ? AND len = ?", key).fetchall())
            self._buckets[key] = bucket
        return bucket

    @lru_cache(maxsize=4096)
    def _variants_cached(self, word, _generation):
        out = {word: 1.0}
        if len(word) < 3:
            return tuple(out.items())    # short words: exact only (neighbours are noise)
        budget = _edit_budget(word)
        freq = {}
        for n in range(len(word) - budget, len(word) + budget + 1):
            freq.update(self._vocab_bucket(word[0], n))
        near = process.extract(word, list(freq), scorer=Levenshtein.distance,
                               score_cutoff=budget, limit=MAX_VARIANTS * 4)
        scored = sorted(((_word_sim(word, w), freq[w], w) for w, _d, _i in near if w != word),
                        key=lambda x: (-x[0], -x[1]))
        for s, _f, w in scored[:MAX_VARIANTS]:
            if s > 0:
                out[w] = s
        return tuple(out.items())

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
                q = f'nt : "{phrase}"' + (" *" if j == n and _prefix_ok(words[-1]) else "")
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
                    if len(w) >= 3 and w not in STOP_WORDS:     # "bob seg*" → Seger; never "the*"
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
        # All of each hit artist's songs by popularity (indexed, cheap). The C-speed
        # pre-filter in identify() trims them before Python scoring — an FTS
        # "artist AND title words" query here was far slower (prefix expansions).
        out = []
        for na in hits:
            out += db.execute(f"SELECT {self._COLS} FROM songs s WHERE s.na = ? ORDER BY s.pop DESC LIMIT ?",
                              (na, ARTIST_SONG_LIMIT)).fetchall()
        return out

    def _candidates(self, words, variants):
        """Songs containing EVERY typed word (each via any of its spellings; the last
        word may be partial), plus leave-one-out queries so one stray word ("that",
        "song", a typo with no close spelling) can't hide the song. Intersections
        are fast; a broad OR over common words is not."""
        db = self._db()
        # Common words ("the", "in", "me") have posting lists covering ~1M songs;
        # intersecting them is the slow part. With 2+ distinctive words, leave them to
        # the phrase query and scoring.
        distinctive = [w for w in words if w not in STOP_WORDS]
        if len(distinctive) >= 2:
            words = distinctive
        groups = []
        for i, w in enumerate(words):
            alts = ['"' + v.replace('"', "") + '"' for v in variants[w]]
            if i == len(words) - 1 and _prefix_ok(w):
                alts.append('"' + w.replace('"', "") + '"*')
            groups.append("(" + " OR ".join(dict.fromkeys(alts)) + ")")
        queries = [groups]
        if len(groups) > 1:
            queries += [groups[:i] + groups[i + 1:] for i in range(len(groups))
                        if not (words[i] in STOP_WORDS and len(groups) > 2)]
        out = []
        for n, g in enumerate(queries[:1 + CANDIDATE_LOO_MAX]):
            if n and len(out) >= LOO_SKIP_IF:
                break    # every word matched plenty already; leave-one-out only rescues sparse queries
            out += db.execute(
                f"SELECT {self._COLS} FROM songs_fts f JOIN songs s ON s.rowid = f.rowid "
                "WHERE songs_fts MATCH ? ORDER BY rank LIMIT ?",
                (" AND ".join(g), CANDIDATE_LIMIT)).fetchall()
        return out

    # ---- step 4: rescoring
    @staticmethod
    def _score(words, qnorm, na, nt, pop, karaoke, optional=frozenset()):
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
            if w in optional:
                wt = FEAT_WORD_WEIGHT
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
        if not self.available:
            return {"status": "none", "best": None, "candidates": [], "unavailable": True}
        optional = frozenset(w for m in _QUERY_FEAT_CLAUSE_RE.finditer(query or "")
                             for w in song_norm(m.group(1)).split())
        qnorm = NOISE_PHRASE_RE.sub(" ", song_norm(_QUERY_FEAT_RE.sub(" ", query or "")))
        words = []
        for w in qnorm.split():
            if w in NOISE_WORDS:
                continue
            # A stray space splitting a word ("mi e" → "mie" ≈ "mine"): glue a lone
            # letter (other than a/i) onto the word before it.
            if len(w) == 1 and w not in "ai" and words and len(words[-1]) >= 2:
                words[-1] += w
                continue
            words.append(w)
        if not words or len("".join(words)) < 3:
            return {"status": "none", "best": None, "candidates": []}
        qnorm = " ".join(words)
        variants = {w: self._variants(w, i == len(words) - 1) for i, w in enumerate(words)}
        seen = {}
        pool = self._candidates(words, variants) + self._phrase_candidates(words) + self._artist_candidates(words, variants)
        pool = list({(r[2], r[3]): r for r in pool}.values())
        if len(pool) > PRESCORE_KEEP:
            hay = [f"{r[2]} {r[3]}" for r in pool]
            keep = process.extract(qnorm, hay, scorer=fuzz.WRatio, limit=PRESCORE_KEEP)
            pool = [pool[i] for _h, _s, i in keep]
        for artist, title, na, nt, pop, karaoke in pool:
            s, detail = self._score(words, qnorm, na, nt, pop, karaoke, optional)
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
        # Credit variants of one song ("Wisin & Yandel" / "Yandel feat. Wisin" —
        # Adore), common in MusicBrainz, are one answer: drop them so they neither
        # crowd the "Which one?" list nor make the best answer look like a close call.
        deduped = []
        for m in ranked[:DEDUPE_TOP]:
            if not any(_same_song(m, k) for k in deduped):
                deduped.append(m)
        ranked = deduped
        best = ranked[0]
        runner = ranked[1] if len(ranked) > 1 else None
        margin = best.score - (runner.score if runner else 0.0)
        if runner and _compact(runner.title) == _compact(best.title):
            # Same title, other artist: popularity decides ("Die Young" → Kesha)…
            lead = (best.pop or 30) - (runner.pop or 30) + (10 if best.karaoke and not runner.karaoke else 0)
            need = SAME_TITLE_POP_LEAD
            # …but a bare, generic title ("baby", "trouble", "rain") shared by several
            # artists is a real question — the wrong guess pre-fills the wrong artist —
            # so only auto-pick when the leader is far more popular.
            same_title = sum(1 for m in ranked if _compact(m.title) == _compact(best.title))
            if best.detail.get("artist_cov", 0) == 0 and same_title >= GENERIC_TITLE_ARTISTS:
                need = GENERIC_TITLE_POP_LEAD
            if lead >= need:
                margin = max(margin, CONFIDENT_MARGIN)
            else:
                margin = min(margin, CONFIDENT_MARGIN - 0.01)
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
