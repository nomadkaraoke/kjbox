// Singer "make it" wizard — karaoke-gen's job submission, inside the singer UI.
//
//   account → email a 6-digit code (a real karaoke-gen account; the finished
//             video + any lyrics-review link go to this inbox)
//   code    → type the code
//   audio   → gen's match-judge tidies the artist/title ("Corrected to X —
//             undo" / "Did you mean…?"), gen's lossless-first audio search
//             finds sources, and the singer picks one — best pick first, every
//             other option one tap away, YouTube link as the last resort.
//
// The picked source becomes a `source_type: "make"` selection for the normal
// confirm → submit screens; the server creates the gen job on submit.
//
// Categorisation / best pick / confidence tiers are ported 1:1 from
// karaoke-gen `frontend/lib/audio-search-utils.ts` and the match-judge flow
// from `components/job/steps/AudioSourceStep.tsx` — keep them in step.

import { t, tn, getLocale } from "./i18n.js";

// --- Ported from karaoke-gen lib/audio-search-utils.ts -----------------------

const CATEGORY_ORDER = [
  "BEST CHOICE", "HI-RES 24-BIT", "STUDIO ALBUMS", "SINGLES", "LIVE VERSIONS",
  "COMPILATIONS", "VINYL RIPS", "SPOTIFY", "YOUTUBE", "OTHER",
];
const CATEGORY_KEY = {
  "BEST CHOICE": "catBest", "HI-RES 24-BIT": "catHiRes", "STUDIO ALBUMS": "catStudio",
  SINGLES: "catSingles", "LIVE VERSIONS": "catLive", COMPILATIONS: "catCompilations",
  "VINYL RIPS": "catVinyl", SPOTIFY: "catSpotify", YOUTUBE: "catYoutube", OTHER: "catOther",
};
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

export function getBestResult(results) {
  if (!results.length) return null;
  let best = null;
  let bestPriority = Infinity;
  for (const r of results) {
    const cat = categorizeResult(r);
    if (cat === "VINYL RIPS") continue;
    const p = BEST_RESULT_PRIORITY.indexOf(cat);
    const eff = p === -1 ? Infinity : p;
    if (eff < bestPriority) { best = r; bestPriority = eff; }
    else if (eff === bestPriority && best && (r.seeders ?? 0) > (best.seeders ?? 0)) best = r;
  }
  return best ?? results[0];
}

export function checkFilenameMismatch(searchTitle, r) {
  const none = { isMismatch: false, filename: "" };
  if ((searchTitle || "").length < 3) return none;
  let filename;
  if (r.target_file) {
    const raw = r.target_file.split("/").pop() || r.target_file;
    filename = raw.replace(/\.[^.]+$/, "").replace(/^\d{1,3}\s*[-.\s]\s*/, "");
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

export function getSearchConfidence(results, searchTitle) {
  if (!results.length) return { tier: 3, best: null, bestCat: null };
  const best = getBestResult(results);
  const bestCat = best ? categorizeResult(best) : null;
  const mismatch = best ? checkFilenameMismatch(searchTitle, best).isMismatch : false;
  const hasLossless = results.some((r) => !["YOUTUBE", "SPOTIFY", "VINYL RIPS"].includes(categorizeResult(r)));
  if (bestCat === "BEST CHOICE" && !mismatch) return { tier: 1, best, bestCat };
  if (!hasLossless) return { tier: 3, best, bestCat };
  if (mismatch && (best.seeders == null || best.seeders < 10)) return { tier: 3, best, bestCat };
  return { tier: 2, best, bestCat };
}

function guidanceTips(results) {
  const prov = (r) => (r.provider || "").toLowerCase();
  const hasFiles = results.some((r) => r.target_file);
  const hasYoutube = results.some((r) => prov(r) === "youtube");
  const hasSpotify = results.some((r) => prov(r) === "spotify");
  const hasLossless = results.some((r) => r.is_lossless && !["youtube", "spotify"].includes(prov(r)));
  const hasVinyl = results.some((r) => (r.quality_data?.media || "").toLowerCase() === "vinyl");
  const hasSeeders = results.some((r) => r.seeders != null);
  const tips = [];
  if (hasFiles) tips.push(t("make.tipFilename"));
  if (hasSeeders) tips.push(t("make.tipAvailability"));
  if (hasLossless) tips.push(t("make.tipStudio"));
  if (hasVinyl) tips.push(t("make.tipVinyl"));
  if (hasYoutube && hasLossless) tips.push(t("make.tipYoutubeLast"));
  if (!hasLossless) tips.push(t("make.tipNoLossless"));
  if (hasSpotify && hasYoutube) tips.push(t("make.tipSpotify"));
  if (!hasLossless && hasYoutube) tips.push(t("make.tipOfficial"));
  return tips;
}

function formatCount(n) {
  if (!n && n !== 0) return "";
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
}

function formatMetadata(r) {
  const parts = [r.release_type, r.year && String(r.year), r.label, r.edition_info, r.quality_data?.media]
    .filter(Boolean);
  return parts.length ? `[${parts.join(" / ")}]` : "";
}

function formatQuality(r) {
  if (r.quality) return r.quality;
  const q = r.quality_data;
  if (!q) return "";
  return [q.format, q.bit_depth && `${q.bit_depth}bit`, q.bitrate && `${q.bitrate}kbps`, q.media]
    .filter(Boolean).join(" ");
}

// gen CATEGORY_CONFIG: rows shown before "+N more", and the header colour.
const CATEGORY_MAX = {
  "BEST CHOICE": 3, "HI-RES 24-BIT": 3, "STUDIO ALBUMS": 3, SINGLES: 2, "LIVE VERSIONS": 2,
  COMPILATIONS: 2, "VINYL RIPS": 2, SPOTIFY: 3, YOUTUBE: 3, OTHER: 3,
};
const CATEGORY_CLASS = {
  "BEST CHOICE": "best", "HI-RES 24-BIT": "hires", "STUDIO ALBUMS": "studio", SINGLES: "singles",
  "LIVE VERSIONS": "live", COMPILATIONS: "comp", "VINYL RIPS": "vinyl", SPOTIFY: "spotify",
  YOUTUBE: "youtube", OTHER: "other",
};

// --- Match-judge (ported from AudioSourceStep.tsx) ---------------------------

const JUDGE_GATE_TIMEOUT_MS = 12000;
const WEAK_TIER = 3;
const isCatalogConfident = (v) => v && v.confident && v.engine === "catalog";

// --- Wizard ------------------------------------------------------------------

export function createMakeFlow(deps) {
  const { el, fetchJson, state, render, BASE, getDeviceId, onPicked, onBack } = deps;

  const m = () => state.make;

  function api(path, body) {
    return fetchJson(`${BASE}/make/${path}`, {
      method: "POST",
      body: JSON.stringify({ device_id: getDeviceId(), locale: getLocale(), ...body }),
    });
  }

  function rerender() {
    if (state.step === "make") render();
  }

  // Entry point: the make card hands over what the singer typed.
  async function start(artist, title) {
    state.make = {
      stage: "loading",
      typed: { artist, title },
      artist, title,
      email: "", code: "", err: "", busy: false,
      search: { status: "idle", results: [], sessionId: null },
      verdict: null, appliedFrom: null, correctionActive: false, gate: false,
      showOthers: false, expanded: new Set(), ytUrl: "", ytErr: "", ytBusy: false,
      searchSeq: 0,
    };
    state.step = "make";
    render();
    try {
      const acct = await fetchJson(`${BASE}/make/account?device_id=${encodeURIComponent(getDeviceId())}`);
      m().email = acct.email || "";
      if (acct.email) beginSearch(); else m().stage = "account";
    } catch {
      m().stage = "account";
    }
    rerender();
  }

  function errText(e) {
    const code = e?.data?.error;
    const map = {
      email_invalid: "make.errEmail", invalid_code: "make.errCode", expired: "make.errCodeExpired",
      too_many_attempts: "make.errTooManyAttempts", too_many_codes: "make.errTooManyCodes",
      signup_cap: "make.errUnavailable", no_credits: "make.errNoCredits", make_limit: "make.errLimit",
      rate_limited: "confirm.tooMany", make_requests_disabled: "confirm.makeDisabled",
    };
    return t(map[code] || "make.errUnavailable");
  }

  function handleAuthLoss(e) {
    if (e?.data?.error === "signin_required") {
      Object.assign(m(), { stage: "account", email: "", err: t("make.errSignedOut") });
      rerender();
      return true;
    }
    return false;
  }

  async function sendCode() {
    const s = m();
    const email = (s.email || "").trim();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) { s.err = t("make.errEmail"); return rerender(); }
    s.busy = true; s.err = ""; rerender();
    try {
      await api("send-code", { email });
      Object.assign(s, { stage: "code", code: "", busy: false });
    } catch (e) {
      Object.assign(s, { busy: false, err: errText(e) });
    }
    rerender();
  }

  async function verifyCode() {
    const s = m();
    const code = (s.code || "").replace(/\s+/g, "");
    if (!/^\d{6}$/.test(code)) { s.err = t("make.errCode"); return rerender(); }
    s.busy = true; s.err = ""; rerender();
    try {
      await api("verify-code", { email: s.email.trim(), code });
      s.busy = false;
      beginSearch();
    } catch (e) {
      Object.assign(s, { busy: false, err: errText(e) });
    }
    rerender();
  }

  async function signOut() {
    try { await api("sign-out", {}); } catch { /* best effort */ }
    Object.assign(m(), { stage: "account", email: "", err: "" });
    rerender();
  }

  // Search + judge. `seq` guards against a stale response after a re-search.
  function beginSearch({ judge = true } = {}) {
    const s = m();
    s.stage = "audio";
    s.err = "";
    s.showOthers = false;
    s.search = { status: "searching", results: [], sessionId: null };
    const seq = ++s.searchSeq;
    if (judge) {
      s.gate = false;
      s.verdict = null;
      s.fastJudge = api("check", { artist: s.artist, title: s.title, stage: "fast" })
        .then((v) => {
          if (seq === s.searchSeq && v.kind === "cosmetic" && v.confident && !v.needs_ai) {
            s.verdict = v;
            applyCorrection(v.canonical_artist, v.canonical_title, false);
          }
          return v;
        })
        .catch((e) => { handleAuthLoss(e); return null; });
    }
    api("search", { artist: s.artist, title: s.title })
      .then((data) => {
        if (seq !== s.searchSeq) return;
        s.search = { status: "done", results: data.results || [], sessionId: data.search_session_id };
        if (judge) runFullJudge(seq); else s.gate = true;
        rerender();
      })
      .catch((e) => {
        if (seq !== s.searchSeq || handleAuthLoss(e)) return;
        s.search = { status: "error", results: [], sessionId: null };
        s.err = errText(e);
        s.gate = true;
        rerender();
      });
    rerender();
  }

  async function runFullJudge(seq) {
    const s = m();
    const tier = getSearchConfidence(s.search.results, s.title).tier;
    const timer = setTimeout(() => { if (seq === s.searchSeq) { s.gate = true; rerender(); } }, JUDGE_GATE_TIMEOUT_MS);
    const fast = await (s.fastJudge || Promise.resolve(null));
    const needFull = !fast || fast.needs_ai || (isCatalogConfident(fast) && tier >= WEAK_TIER);
    if (needFull) {
      try {
        const full = await api("check", { artist: s.artist, title: s.title, stage: "full", tier });
        if (seq === s.searchSeq) applyFullVerdict(full, tier);
      } catch (e) { handleAuthLoss(e); }
    }
    clearTimeout(timer);
    if (seq === s.searchSeq) { s.gate = true; rerender(); }
  }

  function applyFullVerdict(v, tier) {
    const s = m();
    s.verdict = v;
    if (v.kind === "cosmetic" && v.confident) applyCorrection(v.canonical_artist, v.canonical_title, false);
    else if (v.kind === "content" && v.confident) applyCorrection(v.canonical_artist, v.canonical_title, tier >= WEAK_TIER);
  }

  function applyCorrection(artist, title, reSearch) {
    const s = m();
    if (!artist || !title || (artist === s.artist && title === s.title)) return;
    s.appliedFrom = { artist: s.artist, title: s.title };
    s.correctionActive = true;
    s.artist = artist;
    s.title = title;
    if (reSearch) beginSearch({ judge: false });
    rerender();
  }

  function acceptSuggestion(alt) {
    const s = m();
    s.verdict = { kind: "content", confident: true, canonical_artist: alt.artist,
                  canonical_title: alt.title, alternatives: [], engine: "ai" };
    applyCorrection(alt.artist, alt.title, true);
  }

  function toggleCorrection() {
    const s = m();
    if (!s.appliedFrom || !s.verdict) return;
    if (s.correctionActive) {
      s.artist = s.appliedFrom.artist; s.title = s.appliedFrom.title; s.correctionActive = false;
    } else {
      s.artist = s.verdict.canonical_artist; s.title = s.verdict.canonical_title; s.correctionActive = true;
    }
    rerender();
  }

  function pickResult(r) {
    const s = m();
    onPicked({
      source_type: "make",
      source_ref: null,
      song_artist: s.artist,
      song_title: s.title,
      label: `${s.title} — ${s.artist}`,
      source_meta: {
        search_session_id: s.search.sessionId,
        selection_index: r.index,
        audio: resultSummary(r),
      },
    });
  }

  async function pickUrl() {
    const s = m();
    const url = (s.ytUrl || "").trim();
    if (!url) return;
    s.ytBusy = true; s.ytErr = ""; rerender();
    try {
      const v = await api("validate-url", { url });
      s.ytBusy = false;
      if (!v.supported) { s.ytErr = v.detail || t("make.urlUnsupported"); return rerender(); }
      onPicked({
        source_type: "make", source_ref: null,
        song_artist: s.artist, song_title: s.title,
        label: `${s.title} — ${s.artist}`,
        source_meta: { youtube_url: url, audio: t("make.fromYoutubeLink") },
      });
    } catch (e) {
      s.ytBusy = false;
      if (!handleAuthLoss(e)) { s.ytErr = errText(e); rerender(); }
    }
  }

  // ---- Rendering ----------------------------------------------------------

  function resultSummary(r) {
    const cat = categorizeResult(r);
    const who = (r.provider === "YouTube" && r.channel) ? r.channel : (r.artist || "");
    return [r.title, who, t(`make.${CATEGORY_KEY[cat]}`)].filter(Boolean).join(" · ");
  }

  // ---- Result presentation — mirrors gen's AudioSourceStep (PickCard /
  // ResultCategories / ResultRow): the in-torrent FILENAME is what tells a
  // right track from a wrong one (album searches return sibling tracks), and
  // availability decides whether the download succeeds — so both lead.

  function providerPills(r) {
    const out = [];
    const prov = (r.provider || "").toLowerCase();
    if (r.is_lossless) out.push(el("span", { class: "mk-tag mk-tag-lossless" }, t("make.losslessTag")));
    if (prov === "spotify") out.push(el("span", { class: "mk-tag mk-tag-sp" }, "Spotify"));
    else if (prov === "youtube") out.push(el("span", { class: "mk-tag mk-tag-yt" }, "YouTube"));
    return out;
  }

  function availabilityBadge(r) {
    if (r.seeders == null) return null;
    const lvl = r.seeders >= 50 ? "high" : r.seeders >= 10 ? "medium" : "low";
    const key = { high: "availHigh", medium: "availMedium", low: "availLow" }[lvl];
    const tip = { high: "availHighTip", medium: "availMediumTip", low: "availLowTip" }[lvl];
    return el("span", { class: `mk-tag mk-avail-${lvl}`, title: t(`make.${tip}`) }, t(`make.${key}`));
  }

  function viewsBadge(r) {
    if (r.seeders || r.view_count == null) return null;
    const lvl = r.view_count >= 1e6 ? "high" : r.view_count >= 1e5 ? "medium" : "none";
    return el("span", { class: `mk-tag mk-avail-${lvl}` }, t("make.views", { count: formatCount(r.view_count) }));
  }

  function matchBadge(r, showMatch) {
    const mm = checkFilenameMismatch(m().title, r);
    if (!mm.filename) return null;
    if (mm.isMismatch) {
      return el("span", { class: "mk-tag mk-tag-warn", title: t("make.fileLooksLike", { file: mm.filename }) },
        t("make.wrongTrack"));
    }
    return showMatch ? el("span", { class: "mk-tag mk-tag-match" }, t("make.titleMatch")) : null;
  }

  function releaseName(r) {
    const who = (r.provider === "YouTube" && r.channel) ? r.channel : (r.artist || "");
    return [who, r.title].filter(Boolean).join(" - ");
  }

  function confidenceReason(best) {
    const cat = categorizeResult(best);
    const parts = [];
    if (cat === "BEST CHOICE") parts.push(t("make.reasonHighQuality"));
    else if (best.is_lossless) parts.push(t("make.reasonLossless"));
    else if (best.provider === "YouTube") parts.push(t("make.reasonYoutube"));
    else parts.push(t("make.reasonLossy"));
    if (best.title) {
      const from = [best.release_type, best.year].filter(Boolean).join(", ");
      if (from) parts.push(t("make.reasonFrom", { what: from }));
    }
    if (best.seeders != null) {
      parts.push(t(best.seeders >= 50 ? "make.reasonReliable"
        : best.seeders >= 10 ? "make.reasonSlower" : "make.reasonLowAvail"));
    }
    return parts.join(", ");
  }

  function pickWarnings(results, best) {
    const out = [];
    const mm = checkFilenameMismatch(m().title, best);
    if (mm.isMismatch) out.push(t("make.fileLooksLike", { file: mm.filename }));
    const hasLossless = results.some((r) => !["YOUTUBE", "SPOTIFY", "VINYL RIPS"].includes(categorizeResult(r)));
    if (!hasLossless) out.push(t("make.warnNoLossless"));
    else if (categorizeResult(best) === "YOUTUBE") out.push(t("make.warnLossy"));
    if (best.seeders != null && best.seeders < 10) out.push(t("make.warnLowAvail"));
    return out;
  }

  function labelled(label, value, cls) {
    return el("div", { class: "mk-kv" },
      el("span", { class: "mk-kv-label" }, label),
      el("span", { class: `mk-kv-value ${cls || ""}` }, value));
  }

  // The hero "Perfect match found" / "Recommended audio" card.
  function pickCard(conf) {
    const s = m();
    const best = conf.best;
    const perfect = conf.tier === 1;
    const meta = formatMetadata(best);
    return el("div", { class: `mk-pick ${perfect ? "mk-pick-perfect" : "mk-pick-recommended"}`, "data-testid": "make-pick" },
      el("div", { class: "mk-pick-head" },
        el("span", { class: "mk-pick-label" }, perfect ? t("make.perfectMatch") : t("make.recommended")),
        ...providerPills(best)),
      el("div", { class: "mk-pick-body", "data-testid": "make-result" },
        el("p", { class: "mk-reason" }, confidenceReason(best)),
        best.target_file ? labelled(t("make.filename"), best.target_file, "mk-mono") : null,
        labelled(t("make.release"), releaseName(best)),
        el("div", { class: "mk-tags" },
          el("span", { class: "mk-quality" }, formatQuality(best)),
          availabilityBadge(best), viewsBadge(best), matchBadge(best, false)),
        meta ? el("div", { class: "mk-meta" }, meta) : null,
        ...pickWarnings(s.search.results, best).map((w) => el("div", { class: "mk-result-warn" }, "⚠ ", w)),
        el("button", {
          class: "btn primary mk-use-full", "data-testid": "make-use",
          disabled: s.gate ? null : "disabled",
          onclick: () => pickResult(best),
        }, t("make.useThis"))));
  }

  function resultRow(r, showMatch) {
    const s = m();
    const q = formatQuality(r);
    const meta = formatMetadata(r);
    return el("div", { class: "mk-row", "data-testid": "make-result" },
      el("div", { class: "mk-row-main" },
        el("div", { class: "mk-row-line1" },
          ...providerPills(r),
          el("span", { class: "mk-row-name" }, releaseName(r)),
          q ? el("span", { class: "mk-quality" }, `(${q})`) : null,
          availabilityBadge(r), viewsBadge(r), matchBadge(r, showMatch)),
        meta ? el("div", { class: "mk-meta" }, meta) : null,
        r.target_file ? labelled(t("make.filename"), r.target_file, "mk-mono") : null),
      el("button", {
        class: "btn mk-select", disabled: s.gate ? null : "disabled",
        onclick: () => pickResult(r),
      }, t("make.select")));
  }

  function correctionNotice() {
    const s = m();
    const v = s.verdict;
    if (!v) return null;
    if (s.appliedFrom && (v.kind === "cosmetic" || v.kind === "content") && v.confident) {
      const shown = s.correctionActive ? s.appliedFrom : { artist: v.canonical_artist, title: v.canonical_title };
      return el("div", { class: "mk-correction", "data-testid": "make-correction" },
        el("span", {}, s.correctionActive
          ? t("make.correctedTo", { song: `${s.title} — ${s.artist}` })
          : t("make.keptTyped", { song: `${s.title} — ${s.artist}` })),
        " ",
        el("button", { class: "btn link mk-undo", onclick: toggleCorrection },
          s.correctionActive ? t("make.undo") : t("make.useCorrection", { song: `${shown.title} — ${shown.artist}` })),
      );
    }
    const alts = [];
    if (v.kind === "ambiguous" || (v.kind === "content" && !v.confident)) {
      if (v.canonical_artist && v.canonical_title) alts.push({ artist: v.canonical_artist, title: v.canonical_title });
      for (const a of v.alternatives || []) alts.push(a);
    }
    const uniq = alts.filter((a, i) => a.artist && a.title
      && alts.findIndex((b) => b.artist === a.artist && b.title === a.title) === i
      && !(a.artist === s.artist && a.title === s.title)).slice(0, 4);
    if (!uniq.length) return null;
    return el("div", { class: "mk-didyoumean", "data-testid": "make-didyoumean" },
      el("div", { class: "mk-didyoumean-title" }, t("make.didYouMean")),
      ...uniq.map((a) => el("button", { class: "btn ghost mk-suggestion", onclick: () => acceptSuggestion(a) },
        `${a.title} — ${a.artist}`)),
    );
  }

  function fallbackSection(tier3) {
    const s = m();
    const input = el("input", {
      type: "url", class: "sing-empty-input", placeholder: t("empty.youtubePlaceholder"),
      value: s.ytUrl, oninput: (e) => { s.ytUrl = e.target.value; },
    });
    return el("div", { class: "mk-fallback" + (tier3 ? " mk-fallback-first" : ""), "data-testid": "make-fallback" },
      el("h4", {}, t("make.fallbackTitle")),
      el("p", { class: "sing-empty-desc" }, t("make.fallbackDesc")),
      input,
      s.ytErr ? el("p", { class: "error" }, s.ytErr) : null,
      el("button", {
        class: "btn ghost", disabled: (s.ytBusy || !s.gate) ? "disabled" : null, onclick: pickUrl,
      }, s.ytBusy ? t("common.sending") : t("make.useLink")),
    );
  }

  function categoryBlock(g, rows, forceMatch) {
    const s = m();
    const flags = rows.map((r) => checkFilenameMismatch(s.title, r).isMismatch);
    const anyMatch = flags.some((f) => !f);
    const anyMismatch = flags.some((f) => f);
    const showMatch = (anyMatch && anyMismatch) || (forceMatch && anyMatch);
    // Matching filenames first when the category mixes right and wrong tracks.
    const sorted = anyMatch && anyMismatch
      ? rows.map((r, i) => [r, flags[i]]).sort((a, b) => a[1] - b[1]).map((x) => x[0])
      : rows;
    const max = CATEGORY_MAX[g.category] || 3;
    const open = s.expanded.has(g.category);
    const shown = open ? sorted : sorted.slice(0, max);
    const hidden = rows.length - max;
    return el("div", { class: "mk-cat", "data-category": g.category },
      el("div", { class: "mk-cat-head" },
        el("span", { class: `mk-cat-title mk-cat-${CATEGORY_CLASS[g.category] || "other"}` },
          t(`make.${CATEGORY_KEY[g.category]}`)),
        el("span", { class: "mk-cat-count" }, `(${rows.length})`),
        hidden > 0 ? el("button", {
          class: "btn link mk-cat-more",
          onclick: () => { open ? s.expanded.delete(g.category) : s.expanded.add(g.category); rerender(); },
        }, open ? t("make.showLess") : t("make.moreCount", { count: hidden })) : null),
      ...shown.map((r) => resultRow(r, showMatch)));
  }

  function othersSection(conf, grouped) {
    const s = m();
    // Tier 3 has no pick card — every result is listed (like gen).
    const expandedAll = conf.tier === 3;
    const others = s.search.results.filter((r) => expandedAll || r !== conf.best);
    if (!others.length) return null;
    const toggle = expandedAll ? null : el("button", {
      class: "btn link mk-others-toggle", "data-testid": "make-others-toggle",
      onclick: () => { s.showOthers = !s.showOthers; rerender(); },
    }, s.showOthers ? t("make.hideOthers") : tn("make.seeOthers", others.length));
    if (!expandedAll && !s.showOthers) return toggle;
    return el("div", { class: "mk-others" },
      toggle,
      el("p", { class: "hint mk-others-help" },
        expandedAll ? t("make.limitedResults") : t("make.othersHelp")),
      ...grouped.map((g) => {
        const rows = g.results.filter((r) => expandedAll || r !== conf.best);
        return rows.length ? categoryBlock(g, rows, expandedAll) : null;
      }),
    );
  }

  function renderAudio(card) {
    const s = m();
    card.appendChild(el("div", { class: "mk-song" },
      el("div", { class: "mk-song-title" }, s.title),
      el("div", { class: "mk-song-artist" }, s.artist)));
    const notice = correctionNotice();
    if (notice) card.appendChild(notice);

    if (s.search.status === "searching") {
      card.appendChild(el("p", { class: "hint mk-searching", "data-testid": "make-searching" }, t("make.searching")));
      return;
    }
    if (s.search.status === "error") {
      card.appendChild(el("p", { class: "error" }, s.err));
      card.appendChild(el("button", { class: "btn primary", onclick: () => beginSearch() }, t("make.retry")));
      return;
    }
    const results = s.search.results;
    const conf = getSearchConfidence(results, s.title);
    const grouped = groupResults(results);
    if (!s.gate && results.length) card.appendChild(el("p", { class: "hint" }, t("make.checking")));

    if (!results.length) {
      card.appendChild(el("div", { class: "mk-none" },
        el("h4", {}, t("make.noResultsTitle")), el("p", { class: "sing-empty-desc" }, t("make.noResultsDesc"))));
      card.appendChild(fallbackSection(false));
      return;
    }
    if (conf.tier === 3) {
      card.appendChild(el("div", { class: "mk-guidance", "data-testid": "make-guidance" },
        el("div", { class: "mk-guidance-title" }, t("make.limitedTitle")),
        el("ul", {}, ...guidanceTips(results).map((tip) => el("li", {}, tip)))));
      card.appendChild(fallbackSection(true));
    } else {
      card.appendChild(pickCard(conf));
    }
    const others = othersSection(conf, grouped);
    if (others) card.appendChild(others);
    if (conf.tier !== 3) card.appendChild(fallbackSection(false));
  }

  function renderAccount(card) {
    const s = m();
    card.appendChild(el("p", { class: "sing-empty-desc" }, t("make.emailWhy")));
    card.appendChild(el("label", { class: "sing-empty-label" }, t("make.emailLabel"),
      el("input", {
        type: "email", class: "sing-empty-input", autocomplete: "email", inputmode: "email",
        placeholder: "you@example.com", value: s.email, "data-testid": "make-email",
        oninput: (e) => { s.email = e.target.value; },
        onkeydown: (e) => { if (e.key === "Enter") sendCode(); },
      })));
    card.appendChild(el("button", {
      class: "btn primary sing-empty-submit", "data-testid": "make-send-code",
      disabled: s.busy ? "disabled" : null, onclick: sendCode,
    }, s.busy ? t("common.sending") : t("make.sendCode")));
    card.appendChild(el("p", { class: "hint mk-fineprint" }, t("make.emailFineprint")));
  }

  function renderCode(card) {
    const s = m();
    card.appendChild(el("p", { class: "sing-empty-desc" }, t("make.codeSent", { email: s.email })));
    card.appendChild(el("input", {
      type: "text", class: "sing-empty-input mk-code", inputmode: "numeric",
      autocomplete: "one-time-code", maxlength: "6", placeholder: "123456",
      value: s.code, "data-testid": "make-code",
      oninput: (e) => { s.code = e.target.value; if (/^\d{6}$/.test(s.code.trim())) verifyCode(); },
    }));
    card.appendChild(el("button", {
      class: "btn primary sing-empty-submit", "data-testid": "make-verify",
      disabled: s.busy ? "disabled" : null, onclick: verifyCode,
    }, s.busy ? t("common.sending") : t("make.verify")));
    card.appendChild(el("div", { class: "mk-code-actions" },
      el("button", { class: "btn link", onclick: sendCode }, t("make.resend")),
      el("button", { class: "btn link", onclick: () => { s.stage = "account"; s.err = ""; rerender(); } },
        t("make.differentEmail"))));
  }

  function renderStep() {
    const s = m();
    const card = el("main", { class: "sing-card mk-card", "data-testid": "make-step" });
    card.appendChild(el("h2", {}, t("make.title")));
    if (!s) return card;
    if (s.err && s.stage !== "audio") card.appendChild(el("p", { class: "error", "data-testid": "make-error" }, s.err));
    if (s.stage === "loading") card.appendChild(el("p", { class: "hint" }, t("make.loading")));
    else if (s.stage === "account") renderAccount(card);
    else if (s.stage === "code") renderCode(card);
    else renderAudio(card);

    const foot = el("div", { class: "mk-foot" },
      el("button", { class: "btn ghost", onclick: onBack }, t("make.back")));
    if (s.email && s.stage === "audio") {
      foot.appendChild(el("span", { class: "hint mk-signed-in" },
        t("make.signedInAs", { email: s.email }), " ",
        el("button", { class: "btn link", onclick: signOut }, t("make.notYou"))));
    }
    card.appendChild(foot);
    return card;
  }

  return { start, renderStep };
}
