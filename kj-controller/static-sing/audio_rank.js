// Audio-search result ranking — ported 1:1 from karaoke-gen
// `frontend/lib/audio-search-utils.ts` (keep them in step). Pure functions,
// shared by the singer make-it wizard (make.js) and the KJ Gen modal
// (static/app.js, via a dynamic import of /sing/static/audio_rank.js).

export const CATEGORY_ORDER = [
  "BEST CHOICE", "HI-RES 24-BIT", "STUDIO ALBUMS", "SINGLES", "LIVE VERSIONS",
  "COMPILATIONS", "VINYL RIPS", "SPOTIFY", "YOUTUBE", "OTHER",
];
const BEST_RESULT_PRIORITY = [
  "BEST CHOICE", "STUDIO ALBUMS", "HI-RES 24-BIT", "SINGLES", "COMPILATIONS",
  "SPOTIFY", "YOUTUBE", "OTHER",
];

export function categorizeResult(r) {
  const isLossless = r.is_lossless === true;
  const is24Bit = r.quality_data?.bit_depth === 24;
  const seeders = r.seeders ?? 0;
  const provider = (r.provider || "").toLowerCase();
  const releaseType = (r.release_type || "").toLowerCase();
  const media = (r.quality_data?.media || "").toLowerCase();
  if (provider === "spotify") return "SPOTIFY";
  if (provider === "youtube" || !isLossless) return "YOUTUBE";
  if (media === "vinyl") return "VINYL RIPS";
  if (seeders >= 50) return "BEST CHOICE";
  if (is24Bit) return "HI-RES 24-BIT";
  if (releaseType === "live album" || releaseType === "bootleg" || releaseType.includes("live")) return "LIVE VERSIONS";
  if (["compilation", "soundtrack", "anthology"].includes(releaseType)) return "COMPILATIONS";
  if (releaseType === "single" || releaseType === "ep") return "SINGLES";
  if (releaseType === "album" || !releaseType) return "STUDIO ALBUMS";
  return "OTHER";
}

export function groupResults(results) {
  const groups = {};
  for (const r of results) (groups[categorizeResult(r)] ||= []).push(r);
  return CATEGORY_ORDER.filter((c) => groups[c]?.length).map((c) => ({ category: c, results: groups[c] }));
}

// With searchTitle: when any torrent/Spotify result's track name matches it, only
// those compete (a mismatched file is usually a different song). YouTube is never
// promoted this way — video titles nearly always "match". Ties: seeders, then
// popularity (view_count).
export function getBestResult(results, searchTitle = "", searchArtist = "") {
  if (!results.length) return null;
  const titleMatches = searchTitle
    ? results.filter((r) => !["YOUTUBE", "VINYL RIPS"].includes(categorizeResult(r))
        && isConfirmedTitleMatch(searchTitle, r))
    : [];
  // Same title by the requested artist beats a more popular cover/namesake
  // (George Strait's "The Chair" vs Braxton Keith's).
  const artistMatches = searchArtist ? titleMatches.filter((r) => isArtistMatch(searchArtist, r)) : [];
  const pool = artistMatches.length ? artistMatches : titleMatches.length ? titleMatches : results;
  let best = null;
  let bestPriority = Infinity;
  for (const r of pool) {
    const cat = categorizeResult(r);
    if (cat === "VINYL RIPS") continue;
    const p = BEST_RESULT_PRIORITY.indexOf(cat);
    const eff = p === -1 ? Infinity : p;
    if (eff < bestPriority) { best = r; bestPriority = eff; }
    else if (eff === bestPriority && best) {
      const rs = r.seeders ?? 0, bs = best.seeders ?? 0;
      if (rs > bs || (rs === bs && (r.view_count ?? 0) > (best.view_count ?? 0))) best = r;
    }
  }
  return best ?? pool[0];  // all vinyl/live: stay within the title matches
}

// Only real audio extensions — Spotify track names have none ("Mr. Brightside").
const AUDIO_EXT_RE = /\.(flac|mp3|m4a|wav|ogg|opus|aac|alac|ape|wv|aiff?|dsf|mp4|webm)$/i;

export function checkFilenameMismatch(searchTitle, r) {
  const none = { isMismatch: false, filename: "" };
  if ((searchTitle || "").length < 3) return none;
  let filename;
  if (r.target_file) {
    const raw = r.target_file.split("/").pop() || r.target_file;
    filename = raw.replace(AUDIO_EXT_RE, "").replace(/^\d{1,3}\s*[-.\s]\s*/, "");
  } else if (r.title) {
    filename = r.title;
  } else {
    return none;
  }
  const norm = (s) => s.toLowerCase().replace(/[_\-.]+/g, " ").replace(/[^a-z0-9\s]/g, "").replace(/\s+/g, " ").trim();
  const nt = norm(searchTitle);
  const nf = norm(filename);
  if (!nf) return none;
  const shorter = nf.length <= nt.length ? nf : nt;
  const longer = nf.length <= nt.length ? nt : nf;
  if (shorter.length >= 3 && longer.includes(shorter)) return { isMismatch: false, filename };
  return { isMismatch: true, filename };
}

// Whether the result's artist is the requested artist. Lenient on purpose:
// "Braxton Keith, Someone" / "The Killers" vs "Killers" still match.
export function isArtistMatch(searchArtist, r) {
  const norm = (x) => x.toLowerCase().replace(/^the\s+/, "").replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
  const want = norm(searchArtist || "");
  const have = norm(r.artist || "");
  if (want.length < 2 || !have) return false;
  if (have === want || ` ${have} `.includes(` ${want} `) || ` ${want} `.includes(` ${have} `)) return true;
  // Small typos the singer's tidy didn't fix ("radiohed")
  if (want.length < 5) return false;
  return editDistance(want, have) <= Math.floor(want.length / 5);
}

function editDistance(a, b) {
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = cur;
  }
  return prev[b.length];
}

// Whole-title core for equality: drops only TRAILING version suffixes —
// "The Chair (feat. X)", "Hotel California - 2013 Remaster" — so "The Chairman"
// doesn't match "The Chair" and "(I Can't Get No) Satisfaction" stays intact.
function titleCore(x) {
  const norm = (y) => y.toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
  let c = x;
  for (let i = 0; i < 3; i++) {
    const next = c.replace(/\s*[([][^)\]]*[)\]]\s*$/, "").replace(/\s+-\s+[^-]*$/, "");
    if (next === c) break;
    c = next;
  }
  const core = norm(c);
  return core.length >= 3 ? core : norm(x);
}

// True only when the track filename was actually compared and matched —
// checkFilenameMismatch says "no mismatch" when it can't compare (no target_file →
// album title, title < 3 chars, non-Latin filename).
export function isConfirmedTitleMatch(searchTitle, r) {
  if (!r.target_file) return false;
  const m = checkFilenameMismatch(searchTitle, r);
  if (m.isMismatch || !m.filename || (searchTitle || "").length < 3) return false;
  const want = titleCore(searchTitle);
  if (want.length < 3) return false;
  // Compare with and without the track-number strip ("7 Rings" must keep its 7).
  const raw = (r.target_file.split("/").pop() || r.target_file).replace(AUDIO_EXT_RE, "");
  return titleCore(m.filename) === want || titleCore(raw) === want;
}

export function getSearchConfidence(results, searchTitle, searchArtist = "") {
  if (!results.length) return { tier: 3, best: null, bestCat: null };
  const best = getBestResult(results, searchTitle, searchArtist);
  const bestCat = best ? categorizeResult(best) : null;
  const mismatch = best ? checkFilenameMismatch(searchTitle, best).isMismatch : false;
  const hasLossless = results.some((r) => !["YOUTUBE", "SPOTIFY", "VINYL RIPS"].includes(categorizeResult(r)));
  // Spotify is an official release: the right track from it is a good source.
  const spotifyMatch = bestCat === "SPOTIFY" && isConfirmedTitleMatch(searchTitle, best)
    && (!searchArtist || isArtistMatch(searchArtist, best));
  if (bestCat === "BEST CHOICE" && !mismatch) return { tier: 1, best, bestCat, spotifyMatch };
  if (!hasLossless && !spotifyMatch) return { tier: 3, best, bestCat, spotifyMatch };
  if (mismatch && (best.seeders == null || best.seeders < 10)) return { tier: 3, best, bestCat, spotifyMatch };
  return { tier: 2, best, bestCat, spotifyMatch };
}

export function formatCount(n) {
  if (!n && n !== 0) return "";
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
}

export function formatMetadata(r) {
  const parts = [r.release_type, r.year && String(r.year), r.label, r.edition_info, r.quality_data?.media]
    .filter(Boolean);
  return parts.length ? `[${parts.join(" / ")}]` : "";
}

export function formatQuality(r) {
  if (r.quality) return r.quality;
  const q = r.quality_data;
  if (!q) return "";
  return [q.format, q.bit_depth && `${q.bit_depth}bit`, q.bitrate && `${q.bitrate}kbps`, q.media]
    .filter(Boolean).join(" ");
}

// gen CATEGORY_CONFIG: rows shown before "+N more", and the header colour.
export const CATEGORY_MAX = {
  "BEST CHOICE": 3, "HI-RES 24-BIT": 3, "STUDIO ALBUMS": 3, SINGLES: 2, "LIVE VERSIONS": 2,
  COMPILATIONS: 2, "VINYL RIPS": 2, SPOTIFY: 3, YOUTUBE: 3, OTHER: 3,
};
