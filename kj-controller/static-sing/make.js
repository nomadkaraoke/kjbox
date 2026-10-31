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
import {
  CATEGORY_MAX, categorizeResult, checkFilenameMismatch, formatCount, formatMetadata,
  formatQuality, getSearchConfidence, groupResults, isArtistMatch, isConfirmedTitleMatch,
} from "./audio_rank.js";

const CATEGORY_KEY = {
  "BEST CHOICE": "catBest", "HI-RES 24-BIT": "catHiRes", "STUDIO ALBUMS": "catStudio",
  SINGLES: "catSingles", "LIVE VERSIONS": "catLive", COMPILATIONS: "catCompilations",
  "VINYL RIPS": "catVinyl", SPOTIFY: "catSpotify", YOUTUBE: "catYoutube", OTHER: "catOther",
};

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

const CATEGORY_CLASS = {
  "BEST CHOICE": "best", "HI-RES 24-BIT": "hires", "STUDIO ALBUMS": "studio", SINGLES: "singles",
  "LIVE VERSIONS": "live", COMPILATIONS: "comp", "VINYL RIPS": "vinyl", SPOTIFY: "spotify",
  YOUTUBE: "youtube", OTHER: "other",
};

// --- Match-judge (ported from AudioSourceStep.tsx) ---------------------------

const JUDGE_GATE_TIMEOUT_MS = 12000;
// Overridable for deterministic tests (like window.__SING_ARM_MS in sing.js).
const judgeGateMs = () => (typeof window.__SING_JUDGE_GATE_MS === "number"
  ? window.__SING_JUDGE_GATE_MS : JUDGE_GATE_TIMEOUT_MS);
const WEAK_TIER = 3;
const isCatalogConfident = (v) => v && v.confident && v.engine === "catalog";

// The last email this phone signed in with — pre-filled when a sign-in
// expires (gen sessions lapse between weekly shows), so most never retype it.
const EMAIL_KEY = "nk_make_email";
const rememberedEmail = () => { try { return localStorage.getItem(EMAIL_KEY) || ""; } catch { return ""; } };
const rememberEmail = (email) => {
  try { if (email) localStorage.setItem(EMAIL_KEY, email); else localStorage.removeItem(EMAIL_KEY); } catch { /* private mode */ }
};
// "Switch singer" hands the phone to someone else: never offer them this email.
export const forgetMakeEmail = () => rememberEmail("");

// One-tap sign-in SDKs, loaded only when the sign-in block is on screen.
const GSI_SRC = "https://accounts.google.com/gsi/client";
const APPLE_SRC = "https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/appleid.auth.js";
const _scripts = {};
function loadScript(src) {
  if (!_scripts[src]) {
    _scripts[src] = new Promise((resolve, reject) => {
      const tag = document.createElement("script");
      tag.src = src; tag.async = true;
      tag.onload = resolve;
      tag.onerror = () => { delete _scripts[src]; reject(new Error(`load ${src}`)); };
      document.head.appendChild(tag);
    });
  }
  return _scripts[src];
}
// The Google client only trusts sing.nomadkaraoke.com (and Apple's Services ID
// only returns there), so elsewhere (LAN, local, tests) the buttons are hidden.
const socialHostOk = () => window.location.hostname === "sing.nomadkaraoke.com" || !!window.__SING_SOCIAL_STUB;
const APPLE_LOGO_SVG = '<svg class="mk-apple-logo" viewBox="0 0 814 1000" aria-hidden="true">'
  + '<path fill="currentColor" d="M788 341c-6 4-108 62-108 190 0 149 131 202 135 203-1 3-21 72-69 142-43 62-88 '
  + '124-157 124s-87-40-166-40c-77 0-104 41-167 41s-107-57-157-127C40 791 0 680 0 574 0 404 110 314 219 314c58 0 '
  + '106 38 142 38 35 0 89-40 155-40 25 0 115 2 172 29zM554 158c27-32 46-77 46-121 0-6-1-12-2-17-44 2-96 29-127 '
  + '66-25 28-48 73-48 118 0 7 1 14 2 16 3 1 8 1 12 1 39 0 89-27 117-63z"/></svg>';
const onApplePlatform = () => /iPhone|iPad|iPod|Macintosh/.test(navigator.userAgent || "");

// --- Google / Apple sign-in buttons ------------------------------------------
// Shared by the make screen and "Save tonight's songs" (sing.js). `social` is
// /make/account's {google, apple} client ids; `holder` keeps Google's button
// node (an iframe) across re-renders so it isn't rebuilt on every render.
// onToken(provider, idToken) signs in; onError() reports a failed popup.

async function signInWithApple(clientId, onToken, onError) {
  try {
    const stub = window.__SING_SOCIAL_STUB;
    if (stub) return onToken("apple", stub.apple);
    await loadScript(APPLE_SRC);
    window.AppleID.auth.init({
      clientId, scope: "email", usePopup: true,
      redirectURI: `${window.location.origin}/`,
    });
    const r = await window.AppleID.auth.signIn();
    await onToken("apple", r?.authorization?.id_token);
  } catch (e) {
    // Closing the popup is not an error worth shouting about.
    if (e && e.error === "popup_closed_by_user") return;
    onError();
  }
}

function googleButton({ el, social, holder, onToken, testPrefix }) {
  if (holder.googleNode) return holder.googleNode;
  const node = el("div", { class: "mk-google", "data-testid": `${testPrefix}-google` });
  holder.googleNode = node;
  const stub = window.__SING_SOCIAL_STUB;
  if (stub) {
    node.appendChild(el("button", { class: "btn mk-social mk-social-google", type: "button",
      onclick: () => onToken("google", stub.google) }, t("make.continueGoogle")));
    return node;
  }
  loadScript(GSI_SRC).then(() => {
    const gid = window.google && window.google.accounts && window.google.accounts.id;
    if (!gid) throw new Error("gsi");
    gid.initialize({ client_id: social.google, ux_mode: "popup",
                     callback: (resp) => onToken("google", resp && resp.credential) });
    gid.renderButton(node, { type: "standard", theme: "filled_black", size: "large", shape: "pill",
                             text: "continue_with", logo_alignment: "center", locale: getLocale(),
                             width: Math.min(400, node.clientWidth || 320) });
  }).catch(() => { node.hidden = true; });   // blocked / offline: email still works
  return node;
}

function appleButton({ el, social, onToken, onError, testPrefix }) {
  const btn = el("button", { class: "btn mk-social mk-social-apple", type: "button",
    "data-testid": `${testPrefix}-apple`,
    onclick: () => signInWithApple(social.apple, onToken, onError) });
  // Apple's logo as inline SVG (the  glyph only exists in Apple's own fonts).
  btn.innerHTML = APPLE_LOGO_SVG;
  btn.appendChild(document.createTextNode(` ${t("make.continueApple")}`));
  return btn;
}

export function socialButtons({ el, social, holder, onToken, onError, testPrefix = "make" }) {
  if (!socialHostOk() || !social) return null;
  const opts = { el, social, holder, onToken, onError, testPrefix };
  const google = social.google ? googleButton(opts) : null;
  const apple = social.apple ? appleButton(opts) : null;
  if (!google && !apple) return null;
  if (apple && !window.__SING_SOCIAL_STUB) loadScript(APPLE_SRC).catch(() => {});   // so the tap opens the popup at once
  return el("div", { class: "mk-socials" }, ...(onApplePlatform() ? [apple, google] : [google, apple]));
}

// --- Wizard ------------------------------------------------------------------

export function createMakeFlow(deps) {
  const { el, fetchJson, state, render, BASE, getDeviceId, onBack } = deps;
  const { onAccount, onSignedIn, onSignedOut } = deps;
  // One screen, one send: the make screen submits itself (sing.js's shared
  // submitSelection) with the extras the confirm screen used to ask for.
  const { onSubmit, submitErrorText, extrasSlot } = deps;

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

  // Entry point: the make card hands over what the singer typed. `trusted`:
  // artist/title are a confident on-device identification (or a candidate the
  // singer tapped), so gen's AI judge mustn't overrule them — live 2026-10-22 it
  // turned a correct "I Will Be Okay" into a different song, "You Will Be Okay".
  async function start(artist, title, { trusted = false } = {}) {
    state.make = {
      stage: "loading",
      typed: { artist, title },
      artist, title, trusted,
      email: "", code: "", err: "", busy: false,
      search: { status: "idle", results: [], sessionId: null },
      verdict: null, appliedFrom: null, correctionActive: false, gate: false,
      showOthers: false, expanded: new Set(), ytUrl: "", ytErr: "", ytBusy: false,
      searchSeq: 0,
      pick: null, audioOpen: false, sending: false, submitErr: "",
      social: { google: "", apple: "" },
    };
    state.step = "make";
    render();
    try {
      const acct = await fetchJson(`${BASE}/make/account?device_id=${encodeURIComponent(getDeviceId())}`);
      m().email = acct.email || rememberedEmail();
      m().social = { google: acct.google_client_id || "", apple: acct.apple_client_id || "" };
      onAccount?.(acct);
      if (acct.email) beginSearch(); else m().stage = "account";
    } catch {
      m().email = rememberedEmail();
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
      gen_rejected: "make.errRejected", youtube_blocked: "make.errYoutubeBlocked",
    };
    return t(map[code] || "make.errUnavailable");
  }

  function handleAuthLoss(e) {
    if (e?.data?.error === "signin_required") {
      // Keep the email: the singer only has to ask for a new code.
      Object.assign(m(), { stage: "account", email: m().email || rememberedEmail(), err: t("make.errSignedOut") });
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
      const d = await api("verify-code", { email: s.email.trim(), code });
      s.busy = false;
      rememberEmail((d && d.email) || s.email.trim());
      onSignedIn?.((d && d.email) || s.email.trim());
      beginSearch();
    } catch (e) {
      Object.assign(s, { busy: false, err: errText(e) });
    }
    rerender();
  }

  // Google / Apple one-tap: the provider's ID token signs the singer in to gen
  // (kjbox POST /make/social), no email to type and no code to fetch.
  async function socialSignIn(provider, idToken) {
    const s = m();
    if (!idToken || s.busy) return;   // a double tap must not sign in (and search) twice
    s.busy = true; s.err = ""; rerender();
    try {
      const d = await api("social", { provider, id_token: idToken });
      s.busy = false;
      s.email = d.email;
      rememberEmail(d.email);
      onSignedIn?.(d.email);
      beginSearch();
    } catch (e) {
      Object.assign(s, { busy: false, err: e?.data?.error === "social_failed" ? t("make.socialFailed") : errText(e) });
    }
    rerender();
  }

  // What gets sent: the picked result (or YouTube link) as a make selection.
  function selection() {
    const s = m();
    const p = effectivePick();
    if (!p) return null;
    const base = { source_type: "make", source_ref: null, song_artist: s.artist, song_title: s.title,
                   label: `${s.title} — ${s.artist}` };
    if (p.youtube_url) return { ...base, source_meta: { youtube_url: p.youtube_url, audio: t("make.fromYoutubeLink") } };
    return { ...base, source_meta: { search_session_id: s.search.sessionId, selection_index: p.index,
                                     audio: resultSummary(p) } };
  }

  async function submit() {
    const s = m();
    const sel = selection();
    if (!sel || s.sending) return;
    s.sending = true; s.submitErr = ""; rerender();
    try {
      await onSubmit(sel);           // renders the done screen on success
    } catch (e) {
      s.sending = false;
      if (handleAuthLoss(e)) return;
      s.submitErr = submitErrorText(e);
      rerender();
    }
  }

  async function signOut() {
    try { await api("sign-out", {}); } catch { /* best effort */ }
    rememberEmail("");   // "not you?" — don't offer this email to the next person
    onSignedOut?.();
    Object.assign(m(), { stage: "account", email: "", err: "", pick: null,
                         search: { status: "idle", results: [], sessionId: null } });
    rerender();
  }

  // Search + judge. `seq` guards against a stale response after a re-search.
  function beginSearch({ judge = true } = {}) {
    const s = m();
    s.stage = "audio";
    s.err = "";
    s.showOthers = false;
    s.pick = null;          // a new search's results replace any earlier choice
    s.audioOpen = false;
    s.search = { status: "searching", results: [], sessionId: null };
    const seq = ++s.searchSeq;
    if (judge) {
      s.gate = false;
      s.verdict = null;
      s.fastJudge = api("check", { artist: s.artist, title: s.title, stage: "fast" })
        .then((v) => {
          if (seq === s.searchSeq && v.kind === "cosmetic" && v.confident && !v.needs_ai) {
            if (s.gate) { offerVerdict(v); return v; }   // too late to change the song on screen
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
        if (judge && !s.trusted) runFullJudge(seq);
        else if (judge) openGateAfterFastJudge(seq);
        else s.gate = true;
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

  // Trusted identification: only the fast catalog tidy (formatting) may apply,
  // and it lands before the singer can pick — no AI judge.
  async function openGateAfterFastJudge(seq) {
    const s = m();
    // Same time limit as the AI judge's gate: a stalled check never strands the buttons.
    await Promise.race([
      s.fastJudge || Promise.resolve(null),
      new Promise((resolve) => setTimeout(resolve, judgeGateMs())),
    ]);
    if (seq === s.searchSeq) { s.gate = true; rerender(); }
  }

  async function runFullJudge(seq) {
    const s = m();
    const tier = getSearchConfidence(s.search.results, s.title, s.artist).tier;
    const timer = setTimeout(() => { if (seq === s.searchSeq) { s.gate = true; rerender(); } }, judgeGateMs());
    const fast = await (s.fastJudge || Promise.resolve(null));
    const needFull = !fast || fast.needs_ai || (isCatalogConfident(fast) && tier >= WEAK_TIER);
    if (needFull) {
      try {
        const full = await api("check", { artist: s.artist, title: s.title, stage: "full", tier });
        // Too late to change what's on screen: the singer may already be
        // tapping "Use this audio". A late correction is only offered.
        if (seq === s.searchSeq) {
          if (s.gate) offerVerdict(full); else applyFullVerdict(full, tier);
        }
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

  // A correction shown as "Did you mean…?" instead of applied.
  function offerVerdict(v) {
    if (!v || !(v.kind === "cosmetic" || v.kind === "content")) return;
    if (m().appliedFrom) return;   // keep an applied tidy and its undo intact
    m().verdict = { ...v, kind: "content", confident: false };
    rerender();
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
    // A different song: a recording chosen for the old one no longer applies
    // (the automatic pick is re-derived against the new title).
    s.pick = null;
    s.audioOpen = false;
    if (s.correctionActive) {
      s.artist = s.appliedFrom.artist; s.title = s.appliedFrom.title; s.correctionActive = false;
    } else {
      s.artist = s.verdict.canonical_artist; s.title = s.verdict.canonical_title; s.correctionActive = true;
    }
    rerender();
  }

  function pickResult(r) {
    const s = m();
    s.pick = r;
    s.audioOpen = false;
    rerender();
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
      s.pick = { youtube_url: url };
      s.audioOpen = false;
      rerender();
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
    else if (cat === "SPOTIFY") parts.push(t("make.reasonSpotify"));
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
    const spotifyMatch = categorizeResult(best) === "SPOTIFY" && isConfirmedTitleMatch(m().title, best)
      && isArtistMatch(m().artist, best);
    if (!hasLossless && !spotifyMatch) out.push(t("make.warnNoLossless"));
    else if (categorizeResult(best) === "YOUTUBE") out.push(t("make.warnLossy"));
    if (best.seeders != null && best.seeders < 10) out.push(t("make.warnLowAvail"));
    return out;
  }

  function labelled(label, value, cls) {
    return el("div", { class: "mk-kv" },
      el("span", { class: "mk-kv-label" }, label),
      el("span", { class: `mk-kv-value ${cls || ""}` }, value));
  }

  // The audio that will be used — picked automatically for a confident
  // (tier 1/2) search once the checks are done, else the singer's own choice.
  function effectivePick() {
    const s = m();
    if (s.pick) return s.pick;
    if (!s.gate || s.search.status !== "done" || !s.search.results.length) return null;
    const conf = getSearchConfidence(s.search.results, s.title, s.artist);
    return conf.tier === 3 ? null : conf.best;
  }

  // The chosen audio, summarised; "Change" opens the other options.
  function pickCard(r, auto) {
    const s = m();
    if (r.youtube_url) {
      return el("div", { class: "mk-pick mk-pick-perfect", "data-testid": "make-pick" },
        el("div", { class: "mk-pick-head" }, el("span", { class: "mk-pick-label" }, t("make.yourChoice")),
          el("span", { class: "mk-tag mk-tag-yt" }, "YouTube")),
        el("div", { class: "mk-pick-body", "data-testid": "make-result" },
          el("p", { class: "mk-reason mk-mono" }, r.youtube_url),
          changeAudioLink()));
    }
    const perfect = auto && getSearchConfidence(s.search.results, s.title, s.artist).tier === 1;
    const meta = formatMetadata(r);
    return el("div", { class: `mk-pick ${perfect ? "mk-pick-perfect" : "mk-pick-recommended"}`, "data-testid": "make-pick" },
      el("div", { class: "mk-pick-head" },
        el("span", { class: "mk-pick-label" },
          !auto ? t("make.yourChoice") : perfect ? t("make.perfectMatch") : t("make.recommended")),
        ...providerPills(r)),
      el("div", { class: "mk-pick-body", "data-testid": "make-result" },
        el("p", { class: "mk-reason" }, confidenceReason(r)),
        r.target_file ? labelled(t("make.filename"), r.target_file, "mk-mono") : null,
        labelled(t("make.release"), releaseName(r)),
        el("div", { class: "mk-tags" },
          el("span", { class: "mk-quality" }, formatQuality(r)),
          availabilityBadge(r), viewsBadge(r), matchBadge(r, false)),
        meta ? el("div", { class: "mk-meta" }, meta) : null,
        ...pickWarnings(s.search.results, r).map((w) => el("div", { class: "mk-result-warn" }, "⚠ ", w)),
        changeAudioLink()));
  }

  function changeAudioLink() {
    const s = m();
    return el("button", {
      class: "btn link mk-change-audio", "data-testid": "make-change-audio",
      onclick: () => { s.audioOpen = !s.audioOpen; rerender(); },
    }, s.audioOpen ? t("make.hideOthers") : t("make.changeAudio"));
  }

  function resultRow(r, showMatch) {
    const s = m();
    const q = formatQuality(r);
    const meta = formatMetadata(r);
    const chosen = effectivePick() === r;
    return el("div", { class: "mk-row" + (chosen ? " mk-row-chosen" : ""), "data-testid": "make-result" },
      el("div", { class: "mk-row-main" },
        el("div", { class: "mk-row-line1" },
          ...providerPills(r),
          el("span", { class: "mk-row-name" }, releaseName(r)),
          q ? el("span", { class: "mk-quality" }, `(${q})`) : null,
          availabilityBadge(r), viewsBadge(r), matchBadge(r, showMatch)),
        meta ? el("div", { class: "mk-meta" }, meta) : null,
        r.target_file ? labelled(t("make.filename"), r.target_file, "mk-mono") : null),
      el("button", {
        class: "btn mk-select" + (chosen ? " primary" : ""), disabled: s.gate && !chosen ? null : "disabled",
        onclick: () => pickResult(r),
      }, chosen ? t("make.selected") : t("make.select")));
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

  // Every result, grouped like gen's Choose Audio step: the "Change" list, or
  // the list the singer must pick from when no result is a confident match.
  function othersSection(grouped, mustPick) {
    return el("div", { class: "mk-others", "data-testid": "make-others" },
      el("p", { class: "hint mk-others-help" }, mustPick ? t("make.limitedResults") : t("make.othersHelp")),
      ...grouped.map((g) => (g.results.length ? categoryBlock(g, g.results, mustPick) : null)));
  }

  function section(label, ...children) {
    return el("section", { class: "mk-section" }, el("div", { class: "mk-section-label" }, label), ...children);
  }

  function renderAudio() {
    const s = m();
    const out = [];
    if (s.stage !== "audio") {
      out.push(el("p", { class: "hint", "data-testid": "make-audio-later" }, t("make.audioAfterSignIn")));
      return out;
    }
    if (s.search.status === "searching") {
      out.push(el("p", { class: "hint mk-searching", "data-testid": "make-searching" }, t("make.searching")));
      return out;
    }
    if (s.search.status === "error") {
      out.push(el("p", { class: "error" }, s.err));
      out.push(el("button", { class: "btn ghost", onclick: () => beginSearch() }, t("make.retry")));
      return out;
    }
    const results = s.search.results;
    if (!results.length) {
      if (s.pick) out.push(pickCard(s.pick, false));   // the YouTube link they chose
      out.push(el("div", { class: "mk-none" },
        el("h4", {}, t("make.noResultsTitle")), el("p", { class: "sing-empty-desc" }, t("make.noResultsDesc"))));
      out.push(fallbackSection(false));
      return out;
    }
    if (!s.gate) {
      out.push(el("p", { class: "hint", "data-testid": "make-checking" }, t("make.checking")));
      return out;
    }
    const conf = getSearchConfidence(results, s.title, s.artist);
    const grouped = groupResults(results);
    const pick = effectivePick();
    if (conf.tier === 3 && !s.pick) {
      // Not sure which recording is right: the singer picks (never the KJ).
      out.push(el("div", { class: "mk-guidance", "data-testid": "make-guidance" },
        el("div", { class: "mk-guidance-title" }, t("make.pickTitle")),
        el("ul", {}, ...guidanceTips(results).map((tip) => el("li", {}, tip)))));
      out.push(othersSection(grouped, true));
      out.push(fallbackSection(true));
      return out;
    }
    out.push(pickCard(pick, !s.pick));
    if (s.audioOpen) {
      out.push(othersSection(grouped, false));
      out.push(fallbackSection(false));
    }
    return out;
  }

  function renderSignIn() {
    const s = m();
    if (s.stage === "audio" && s.email) {
      return [el("p", { class: "mk-signed-in", "data-testid": "make-signed-in" },
        t("make.sendingTo", { email: s.email }), " ",
        el("button", { class: "btn link", onclick: signOut }, t("make.notYou")))];
    }
    if (s.stage === "loading") return [el("p", { class: "hint" }, t("make.loading"))];
    const out = [el("p", { class: "sing-empty-desc" }, t("make.sendToDesc"))];
    const socials = socialButtons({
      el, social: s.social, holder: s, onToken: socialSignIn,
      onError: () => { s.err = t("make.socialFailed"); rerender(); },
    });
    if (socials) {
      out.push(socials);
      out.push(el("div", { class: "mk-or" }, el("span", {}, t("make.orEmail"))));
    }
    if (s.stage === "code") {
      out.push(el("p", { class: "sing-empty-desc" }, t("make.codeSent", { email: s.email })));
      out.push(el("input", {
        type: "text", class: "sing-empty-input mk-code", inputmode: "numeric",
        autocomplete: "one-time-code", maxlength: "6", placeholder: "123456",
        value: s.code, "data-testid": "make-code",
        oninput: (e) => { s.code = e.target.value; if (/^\d{6}$/.test(s.code.trim())) verifyCode(); },
      }));
      out.push(el("button", {
        class: "btn ghost sing-empty-submit", "data-testid": "make-verify",
        disabled: s.busy ? "disabled" : null, onclick: verifyCode,
      }, s.busy ? t("common.sending") : t("make.verify")));
      out.push(el("div", { class: "mk-code-actions" },
        el("button", { class: "btn link", onclick: sendCode }, t("make.resend")),
        el("button", { class: "btn link", onclick: () => { s.stage = "account"; s.err = ""; rerender(); } },
          t("make.differentEmail"))));
    } else {
      // A real <form> with name/autocomplete=email: the browser offers the
      // phone's saved addresses, so most singers tap instead of typing.
      out.push(el("form", {
        class: "mk-email-form", autocomplete: "on",
        onsubmit: (e) => { e.preventDefault(); sendCode(); },
      },
        el("input", {
          type: "email", name: "email", id: "mk-email", class: "sing-empty-input", autocomplete: "email",
          inputmode: "email", autocapitalize: "off", spellcheck: "false",
          placeholder: t("make.emailPlaceholder"), "aria-label": t("make.emailLabel"),
          value: s.email, "data-testid": "make-email",
          oninput: (e) => { s.email = e.target.value; },
        }),
        el("button", {
          type: "submit", class: "btn ghost", "data-testid": "make-send-code",
          disabled: s.busy ? "disabled" : null,
        }, s.busy ? t("common.sending") : t("make.sendCode"))));
    }
    if (s.err) out.push(el("p", { class: "error", "data-testid": "make-error" }, s.err));
    return out;
  }

  // The one "send" on the screen, pinned above the tab bar. Disabled until it
  // can work, and its label says what's still missing.
  function submitBar() {
    const s = m();
    let label = t("make.submitCta");
    let ready = false;
    if (s.sending) label = t("common.sending");
    else if (s.stage !== "audio") label = t("make.needSignIn");
    else if (s.search.status === "searching" || (!s.gate && s.search.status === "done")) label = t("make.findingAudio");
    else if (!effectivePick()) label = t("make.needAudio");
    else ready = true;
    return el("div", { class: "mk-submit-bar", "data-testid": "make-submit-bar" },
      el("button", {
        class: "btn primary mk-submit", "data-testid": "make-submit",
        disabled: ready ? null : "disabled", onclick: submit,
      }, label));
  }

  function renderStep() {
    const s = m();
    const card = el("main", { class: "sing-card mk-card mk-one", "data-testid": "make-step" });
    card.appendChild(el("h2", {}, t("make.screenTitle")));
    if (!s) return card;
    card.appendChild(section(t("make.songLabel"),
      el("div", { class: "mk-song" },
        el("div", { class: "mk-song-title" }, s.title),
        el("div", { class: "mk-song-artist" }, s.artist)),
      correctionNotice(),
      el("button", { class: "btn link mk-change-song", "data-testid": "make-change-song", onclick: onBack },
        t("make.changeSong"))));
    card.appendChild(section(t("make.sendToTitle"), ...renderSignIn()));
    card.appendChild(section(t("make.audioLabel"), ...renderAudio()));
    const extras = extrasSlot ? extrasSlot() : null;
    if (extras) card.appendChild(extras);
    if (s.submitErr) card.appendChild(el("p", { class: "error", "data-testid": "make-submit-error" }, s.submitErr));
    card.appendChild(el("div", { class: "mk-foot" },
      el("button", { class: "btn ghost", onclick: onBack }, t("make.back"))));
    card.appendChild(submitBar());
    return card;
  }

  return { start, renderStep };
}
