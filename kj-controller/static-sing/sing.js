// Nomad Karaoke — singer-facing request SPA.
// Vanilla JS, no framework, no build step. Mobile-first.

// Blueprint mount point. On the public host (sing.nomadkaraoke.com) the singer
// UI lives at `/` via a WSGI rewrite; on the admin host it's under `/sing/`.
// Detect at runtime so fetches and SW registration work in both places.
const BASE = window.location.pathname.startsWith("/sing/") ? "/sing" : "";

const root = document.getElementById("sing-root");
const codeEntryEl = document.getElementById("sing-enter-code");

let TOKEN = "";
let INITIAL_REQUEST_ID = "";
let INITIAL_MAKE_REQUESTS_ENABLED = true;
let INITIAL_SIMPLE_MODE = false;
if (root) {
  TOKEN = root.dataset.token;
  INITIAL_REQUEST_ID = root.dataset.requestId;
  // Phase C — landing carries the flag so the empty-state triage has it
  // before the first /sing/search completes. Absence or "1" = enabled.
  INITIAL_MAKE_REQUESTS_ENABLED = root.dataset.makeRequestsEnabled !== "0";
  // Simple KJ Mode — landing forwards this so the SPA can trim its UI on
  // first paint (no triage cards, no kj_pick deferral). "1" = on.
  INITIAL_SIMPLE_MODE = root.dataset.simpleMode === "1";
}

const LS = {
  get: (k) => { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};

// Tracks the singer's submitted request ids for the current token so the
// done screen can render a multi-song "your night" list. We scope by token
// so that yesterday's ids don't leak into tonight's event.
const MY_REQUESTS_KEY = "sing_my_request_ids";
// Mirror the server's /sing/my-requests id cap. The stored id list grows across
// a persistent event token (a New Rotation doesn't rotate it), so we send only
// the most-recent ids — sending more would 400 and blank the list for a
// returning singer. The server also night-scopes the rows it returns.
const MY_REQUESTS_MAX = 20;

function _readMyRequestStore() {
  try {
    const raw = localStorage.getItem(MY_REQUESTS_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (obj && typeof obj === "object" && Array.isArray(obj.ids)) return obj;
    return null;
  } catch { return null; }
}

function rememberRequestId(token, id) {
  if (!token || !id) return;
  let store = _readMyRequestStore();
  if (!store || store.token !== token) store = { token, ids: [] };
  if (!store.ids.includes(id)) store.ids.push(id);
  try { localStorage.setItem(MY_REQUESTS_KEY, JSON.stringify(store)); }
  catch { /* private browsing — best-effort */ }
}

function readMyRequestIds(token) {
  const store = _readMyRequestStore();
  if (!store || store.token !== token) return [];
  return store.ids.slice();
}

const PHONE_RE = /^\+?[0-9 \-()]{7,20}$/;

const state = {
  step: "landing",
  name: LS.get("sing_name"),
  phone: LS.get("sing_phone"),
  query: "",
  selected: null,   // { source_type, source_ref, song_artist, song_title, label }
  makeArtist: "",
  makeTitle: "",
  // New: duet partners typed on the confirm screen. Array of
  // {name, phone}. Capped at MAX_PARTNERS in the render.
  additional: [],
  request: null,    // after submit
  rotationCache: null,   // {fetchedAt: number, payload: object} — survives back-from-search
  // Phase C — updated on every /sing/search response so a KJ flipping the
  // toggle mid-session takes effect on the next keystroke-triggered search.
  makeRequestsEnabled: INITIAL_MAKE_REQUESTS_ENABLED,
  // Simple KJ Mode — restricts source allowlist and trims UI; mirrors
  // server's kj_simple_mode flag, kept in sync via /sing/search response.
  simpleMode: INITIAL_SIMPLE_MODE,
};

const MAX_PARTNERS = 3;

// --- Offline detection -----------------------------------------------------

let consecutivePollFailures = 0;
const OFFLINE_FAIL_THRESHOLD = 2;

function setOfflineBanner(visible) {
  const banner = document.getElementById("sing-offline");
  if (!banner) return;
  if (visible) banner.removeAttribute("hidden");
  else banner.setAttribute("hidden", "");
}

function onPollSuccess() {
  consecutivePollFailures = 0;
  setOfflineBanner(false);
}

function onPollFailure() {
  consecutivePollFailures++;
  if (consecutivePollFailures >= OFFLINE_FAIL_THRESHOLD) setOfflineBanner(true);
}

window.addEventListener("online", () => setOfflineBanner(false));
window.addEventListener("offline", () => setOfflineBanner(true));

// --- Network ---------------------------------------------------------------

async function fetchJson(url, opts = {}) {
  const urlWithToken = url.includes("?")
    ? `${url}&t=${encodeURIComponent(TOKEN)}`
    : `${url}?t=${encodeURIComponent(TOKEN)}`;
  const resp = await fetch(urlWithToken, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  let data = null;
  try { data = await resp.json(); } catch { data = null; }
  if (!resp.ok) {
    const err = new Error((data && data.error) || resp.statusText);
    err.status = resp.status;
    err.data = data;
    throw err;
  }
  return data;
}

async function search(query) {
  const q = encodeURIComponent(query);
  return fetchJson(`${BASE}/search?q=${q}`);
}

async function submit(payload) {
  return fetchJson(`${BASE}/submit`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// --- Render helpers --------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v != null) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function render() {
  if (nowPlayingTimer && state.step !== "landing" && state.step !== "done") {
    clearInterval(nowPlayingTimer);
    nowPlayingTimer = null;
  }
  if (state._statusPollTimer && state.step !== "done") {
    clearInterval(state._statusPollTimer);
    state._statusPollTimer = null;
  }
  root.innerHTML = "";
  const view = {
    landing: renderLanding,
    identity: renderIdentity,
    search: renderSearch,
    confirm: renderConfirm,
    done: renderDone,
  }[state.step] || renderLanding;
  root.appendChild(view());
}

function back(to) {
  return () => { state.step = to; render(); };
}

// --- Views -----------------------------------------------------------------

// --- "What's playing now" widget -------------------------------------------

let nowPlayingTimer = null;

function renderNowPlaying() {
  const node = el("div", { class: "now-playing", "data-loading": "true" },
    el("div", { class: "np-loading" }, "Checking rotation…"),
  );
  fetchNowPlaying(node);
  return node;
}

async function fetchNowPlaying(node) {
  if (nowPlayingTimer) { clearInterval(nowPlayingTimer); nowPlayingTimer = null; }
  const tick = async () => {
    try {
      const resp = await fetch(`${BASE}/now?t=${encodeURIComponent(TOKEN)}`, {
        credentials: "same-origin",
      });
      if (!resp.ok) { onPollFailure(); return renderNowError(node); }
      onPollSuccess();
      updateNowPlaying(node, await resp.json());
    } catch {
      onPollFailure();
      renderNowError(node);
    }
  };
  await tick();
  nowPlayingTimer = setInterval(tick, 15000);
}

function updateNowPlaying(node, data) {
  node.innerHTML = "";
  node.removeAttribute("data-loading");
  const { now_singing, up_next, queued_count } = data || {};
  if (!now_singing && !up_next && !queued_count) {
    node.appendChild(el("div", { class: "np-empty" },
      "Rotation hasn't started yet — you could be the first!"));
    return;
  }
  if (now_singing) {
    node.appendChild(el("div", { class: "np-line np-now" },
      el("span", { class: "np-label" }, "🎤 Now:"),
      el("span", { class: "np-singer" }, now_singing.first_name || "—"),
      now_singing.song_artist
        ? el("span", { class: "np-song" }, `— ${now_singing.song_artist}`)
        : null,
    ));
  }
  if (up_next) {
    node.appendChild(el("div", { class: "np-line np-next" },
      el("span", { class: "np-label" }, "Up next:"),
      el("span", { class: "np-singer" }, up_next.first_name || "—"),
    ));
  } else if (!now_singing && queued_count) {
    node.appendChild(el("div", { class: "np-line" },
      "Between singers — next up soon"));
  }
}

function renderNowError(node) {
  node.innerHTML = "";
  node.removeAttribute("data-loading");
  // Silent failure — don't clutter the card while the status poll still has a chance.
}

// --- Full rotation expander -----------------------------------------------

const ROTATION_CACHE_TTL_MS = 30000;

async function fetchRotation() {
  const t = encodeURIComponent(TOKEN);
  const resp = await fetch(`${BASE}/rotation?t=${t}`, { credentials: "same-origin" });
  if (!resp.ok) {
    const err = new Error("rotation fetch failed");
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}

function _waitText(entry) {
  // Apply rules in order — first match wins.
  if (entry.now_singing) return "on now";
  if (entry.position === 1) return "up next";
  // Position 2 is "up next" only when there is actually someone on stage at #1.
  // Detected via the cached payload: the caller passes a hasNowSinging flag.
  if (entry._hasNowSinging && entry.position === 2) return "up next";
  const low = Math.round(entry.range_low_s / 60);
  const high = Math.round(entry.range_high_s / 60);
  return `~${low}–${high} min`;
}

function _formatUpdatedAt(fetchedAt) {
  const ageS = Math.round((Date.now() - fetchedAt) / 1000);
  if (ageS < 10) return "updated just now";
  if (ageS < 60) return `updated ${ageS}s ago`;
  const ageM = Math.round(ageS / 60);
  return ageM === 1 ? "updated 1 min ago" : `updated ${ageM} min ago`;
}

function _renderRotationBody(payload) {
  const body = el("div", { class: "rotation-body" });
  body.appendChild(el("p", { class: "rotation-caveat" },
    el("em", {},
      "Order can change — new singers get bumped up, paid spots jump ahead, "
      + "and times are rough. Treat this as a guide."),
  ));

  const entries = payload?.entries || [];
  if (entries.length === 0) {
    body.appendChild(el("p", { class: "rotation-empty" },
      "Rotation hasn't started yet — you could be the first!"));
    return body;
  }

  const hasNowSinging = entries.some((e) => e.now_singing);
  const list = el("ol", { class: "rotation-list" });
  for (const entry of entries) {
    const augmented = { ...entry, _hasNowSinging: hasNowSinging };
    list.appendChild(el("li", { class: "rotation-row" },
      el("span", { class: "rotation-pos" }, `#${entry.position}`),
      el("span", { class: "rotation-name" },
        entry.now_singing ? `🎤 ${entry.first_name || "—"}` : (entry.first_name || "—")),
      el("span", { class: "rotation-song" }, entry.song_artist || ""),
      el("span", { class: "rotation-wait" }, _waitText(augmented)),
    ));
  }
  body.appendChild(list);
  body.appendChild(el("p", { class: "rotation-updated" },
    _formatUpdatedAt(payload._fetchedAt)));
  return body;
}

function _renderRotationLoading() {
  return el("div", { class: "rotation-body" },
    el("p", { class: "rotation-loading" }, "Loading rotation…"));
}

function _renderRotationError(status) {
  const body = el("div", { class: "rotation-body" });
  if (status === 403) {
    body.appendChild(el("p", { class: "rotation-error" },
      "Requests just closed — ask the KJ."));
  } else {
    body.appendChild(el("p", { class: "rotation-error" },
      "Couldn't load rotation — close and tap again to retry."));
  }
  return body;
}

function _updateRotationSummary(detailsEl, count) {
  const summary = detailsEl.querySelector("summary");
  if (!summary) return;
  summary.textContent = count > 0
    ? `See full rotation (${count} ${count === 1 ? "singer" : "singers"})`
    : "See full rotation";
}

function renderRotationExpander() {
  const details = el("details", { class: "rotation-expander" },
    el("summary", {}, "See full rotation"),
    el("div", { class: "rotation-body" }),  // placeholder; populated on toggle
  );

  // If we already have a fresh cache, populate the body up-front so that
  // returning to the landing screen after Back-from-Search renders instantly
  // when the user re-expands.
  if (state.rotationCache && Date.now() - state.rotationCache.fetchedAt < ROTATION_CACHE_TTL_MS) {
    const payload = { ...state.rotationCache.payload, _fetchedAt: state.rotationCache.fetchedAt };
    details.querySelector(".rotation-body").replaceWith(_renderRotationBody(payload));
    _updateRotationSummary(details, payload.entries?.length || 0);
  }

  details.addEventListener("toggle", async () => {
    if (!details.open) return;
    // Serve from cache if fresh.
    if (state.rotationCache && Date.now() - state.rotationCache.fetchedAt < ROTATION_CACHE_TTL_MS) {
      const payload = { ...state.rotationCache.payload, _fetchedAt: state.rotationCache.fetchedAt };
      details.querySelector(".rotation-body").replaceWith(_renderRotationBody(payload));
      _updateRotationSummary(details, payload.entries?.length || 0);
      return;
    }
    // Otherwise refetch.
    details.querySelector(".rotation-body").replaceWith(_renderRotationLoading());
    try {
      const payload = await fetchRotation();
      const fetchedAt = Date.now();
      state.rotationCache = { fetchedAt, payload };
      details.querySelector(".rotation-body").replaceWith(
        _renderRotationBody({ ...payload, _fetchedAt: fetchedAt }),
      );
      _updateRotationSummary(details, payload.entries?.length || 0);
    } catch (e) {
      details.querySelector(".rotation-body").replaceWith(_renderRotationError(e.status));
    }
  });

  return details;
}

function renderLanding() {
  return el("main", { class: "sing-card" },
    renderNowPlaying(),   // Task 5 populates this; stub is harmless
    renderRotationExpander(),
    el("h1", {}, "Request a song"),
    el("p", {},
      "Tap below to add your song to the rotation. The KJ will call you up when you're on."),
    el("button", {
      class: "btn primary",
      onclick: () => {
        // Phone is optional — only the name gates progression. If present,
        // it must still parse (defence against a corrupted LS value).
        const phoneOk = !state.phone || PHONE_RE.test(state.phone);
        state.step = state.name && phoneOk ? "search" : "identity";
        render();
      },
    }, state.name ? "Continue" : "Get started"),
    state.name ? el("p", { class: "hint" },
      `Not ${state.name}? `,
      el("a", { href: "#", onclick: (e) => {
        e.preventDefault();
        state.name = state.phone = "";
        LS.set("sing_name", ""); LS.set("sing_phone", "");
        state.step = "identity"; render();
      } }, "switch")
    ) : null,
  );
}

function renderIdentity() {
  // Store typed-but-not-yet-submitted values on `state` so a validation-fail
  // rerender preserves them. Fall back to persisted state.name / state.phone
  // on first entry.
  if (state._identityDraft == null) {
    state._identityDraft = { name: state.name, phone: state.phone, err: "" };
  }
  const draft = state._identityDraft;

  const onSubmit = (e) => {
    e.preventDefault();
    if (!draft.name.trim()) {
      draft.err = "Please enter your name.";
      rerender(); return;
    }
    // Phone is optional — only validate format when present.
    const phoneTrimmed = draft.phone.trim();
    if (phoneTrimmed && !PHONE_RE.test(phoneTrimmed)) {
      draft.err = "Please enter a valid phone number (digits, spaces, or + allowed), or leave it blank.";
      rerender(); return;
    }
    state.name = draft.name.trim();
    state.phone = phoneTrimmed;
    LS.set("sing_name", state.name);
    LS.set("sing_phone", state.phone);
    state._identityDraft = null;
    state.step = "search";
    render();
  };

  function rerender() {
    root.innerHTML = "";
    root.appendChild(renderIdentity());
  }

  return el("main", { class: "sing-card" },
    el("h2", {}, "Your details"),
    el("form", { onsubmit: onSubmit },
      el("label", {}, "First name + last initial",
        el("input", {
          type: "text", autocomplete: "given-name",
          value: draft.name, placeholder: "e.g. Andrew B.",
          oninput: (e) => { draft.name = e.target.value; },
        }),
      ),
      el("label", {}, "Phone number (optional)",
        el("input", {
          type: "tel", autocomplete: "tel",
          value: draft.phone, placeholder: "+61 400 123 456",
          oninput: (e) => { draft.phone = e.target.value; },
        }),
        el("span", { class: "hint" },
          "By providing your number, you agree to receive a one-off SMS when you're up to sing. Msg & data rates may apply. Reply STOP to opt out. Leave blank to skip — the KJ will just call your name."),
      ),
      draft.err ? el("p", { class: "error" }, draft.err) : null,
      el("div", { class: "row" },
        el("button", { type: "button", class: "btn ghost", onclick: () => { state._identityDraft = null; state.step = "landing"; render(); } }, "Back"),
        el("button", { type: "submit", class: "btn primary" }, "Next"),
      ),
    ),
  );
}

// Phase B — Commercial vs Community explainer is dismissed once per
// browser. localStorage may be unavailable in private browsing; treat a
// throw as "never seen".
const RULES_CC_SEEN_KEY = "sing_rules_commercial_community_seen";
function _ccExplainerSeen() {
  try { return localStorage.getItem(RULES_CC_SEEN_KEY) === "1"; }
  catch (e) { return false; }
}
function _markCcExplainerSeen() {
  try { localStorage.setItem(RULES_CC_SEEN_KEY, "1"); }
  catch (e) { /* private browsing — reshows are acceptable */ }
}

function _humanFileSize(bytes) {
  if (!bytes || bytes < 0) return "";
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(0)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(0)} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}

function _versionSection(version) {
  if (version.source === "local") return "library";
  const kn = version.kn || {};
  if (kn.divebar && kn.divebar.file_id) return "divebar";
  if (kn.is_community) return "community";
  return "online";
}

function renderSearch() {
  let results = { songs: [] };
  let loading = false;
  let err = "";
  // Phase B — group keys the singer has expanded. Persists across re-renders
  // triggered by search keystrokes but resets on back/forward navigation.
  const expandedSongs = new Set();
  let ccExplainerDismissed = _ccExplainerSeen();

  // Anti-mis-tap: a freshly-(re)rendered results list is inert for a moment, so
  // a tap aimed at the previous layout can't activate a row that just appeared
  // (the search backend is slow, so rows can arrive right as a finger lands).
  // The window is overridable via window.__SING_ARM_MS for deterministic tests
  // (mirrors the existing window.__sing_* test bridge).
  const armMs = () =>
    (typeof window.__SING_ARM_MS === "number" ? window.__SING_ARM_MS : 300);
  let armAt = 0;
  const armed = () => Date.now() >= armAt;

  let debounceTimer = null;
  // Generation guard (ported from the KJ link search, app.js rotSearchGen):
  // bumped at the start of each fetch so a slower earlier query cannot clobber
  // a newer one, and so only the latest search may clear loading / set err.
  let searchGen = 0;
  const doSearch = (q) => {
    clearTimeout(debounceTimer);
    // 700ms (was 300) to match the KJ side — the shared backend live-scrapes,
    // so a longer debounce just trims wasted scrapes. Correctness comes from
    // the generation guard below, not from the delay.
    debounceTimer = setTimeout(async () => {
      const myGen = ++searchGen;
      if (q.trim().length < 3) {
        results = { songs: [] };
        if (myGen === searchGen) { loading = false; update(); }
        return;
      }
      loading = true; err = ""; update();
      try {
        const data = await search(q.trim());
        if (myGen !== searchGen) return;   // superseded — discard stale response
        results = data;
        // Phase C — mirror the server's current flag so a mid-session KJ
        // toggle takes effect on the next search without a page reload.
        if (typeof data.make_requests_enabled === "boolean") {
          state.makeRequestsEnabled = data.make_requests_enabled;
        }
        if (typeof data.simple_mode === "boolean") {
          state.simpleMode = data.simple_mode;
        }
      } catch (e) {
        if (myGen !== searchGen) return;   // stale failure — don't clobber a live search
        err = "Search failed. Try again.";
      } finally {
        if (myGen === searchGen) { loading = false; update(); }  // latest-owner rule
      }
    }, 700);
  };

  // Single-version short-circuit — when a group has exactly one version, we
  // skip the "KJ picks" framing and bind the concrete source immediately.
  // Mirrors the old pickLocal / pickKN flow.
  const pickSingleLocal = (localRow) => {
    state.selected = {
      source_type: "local",
      source_ref: localRow.path,
      song_artist: localRow.artist || "",
      song_title: localRow.title || "",
      label: `${localRow.title || localRow.filename} — ${localRow.artist || ""} (in library)`,
    };
    state.step = "confirm"; render();
  };
  const pickSingleKN = (group, track) => {
    const hasDivebar = !!(track.divebar && track.divebar.file_id);
    const youtubeUrl = track.youtube_url || track.url || "";
    state.selected = hasDivebar
      ? {
          source_type: "divebar",
          source_ref: track.divebar.file_id,
          song_artist: group.artist,
          song_title: group.title,
          label: `${group.title} — ${group.artist} (community karaoke)`,
          source_meta: { brand_code: track.brand_code, disc_id: track.disc_id, format: track.divebar.format },
        }
      : {
          source_type: "kn",
          source_ref: youtubeUrl,
          song_artist: group.artist,
          song_title: group.title,
          label: `${group.title} — ${group.artist} (online karaoke)`,
          source_meta: { brand_code: track.brand_code, disc_id: track.disc_id },
        };
    state.step = "confirm"; render();
  };

  // Multi-version: defer to the KJ. The full versions snapshot rides along
  // in source_meta so the admin approval UI can render the picker without a
  // fresh search.
  const pickKjChoice = (group) => {
    state.selected = {
      source_type: "kj_pick",
      source_ref: null,
      song_artist: group.artist,
      song_title: group.title,
      label: `${group.title} — ${group.artist} (KJ picks best version)`,
      source_meta: {
        group_key: group.key,
        version_count: group.version_count,
        versions: group.versions,
      },
    };
    state.step = "confirm"; render();
  };

  const pickMake = () => {
    state.selected = {
      source_type: "make",
      source_ref: null,
      song_artist: state.makeArtist,
      song_title: state.makeTitle,
      label: `Ask the KJ to make: ${state.makeTitle} — ${state.makeArtist}`,
    };
    state.step = "confirm"; render();
  };

  const pickYouTube = (url) => {
    state.selected = {
      source_type: "youtube",
      source_ref: url,
      song_artist: "",
      song_title: "",
      label: `YouTube: ${url}`,
    };
    state.step = "confirm"; render();
  };

  function update() {
    const card = root.querySelector(".sing-card");
    const resultsEl = card?.querySelector(".results");
    if (resultsEl) resultsEl.replaceWith(renderResults());
  }

  function pickSingleVersion(group) {
    // Only called when group.version_count === 1.
    const v = group.versions[0];
    if (v.source === "local") return pickSingleLocal(v.local);
    if (v.source === "kn") return pickSingleKN(group, v.kn);
  }

  // Phase B — a nerd picking a specific version from the expander. The
  // submission is NOT a kj_pick; it's a direct concrete-source request, so
  // auto-approve (if the KJ enables it) still works and the admin sees the
  // usual "Approve" flow — no picker shown.
  function pickSpecificVersion(group, version) {
    if (version.source === "local") {
      const local = version.local || {};
      state.selected = {
        source_type: "local",
        source_ref: local.path,
        song_artist: local.artist || group.artist,
        song_title: local.title || group.title,
        label: `${group.title} — ${group.artist} (${local.filename || "library"})`,
      };
    } else {
      const kn = version.kn || {};
      const hasDivebar = !!(kn.divebar && kn.divebar.file_id);
      if (hasDivebar) {
        state.selected = {
          source_type: "divebar",
          source_ref: kn.divebar.file_id,
          song_artist: group.artist,
          song_title: group.title,
          label: `${group.title} — ${group.artist} (${kn.brand_name || kn.brand_code || "community"})`,
          source_meta: {
            brand_code: kn.brand_code,
            disc_id: kn.divebar.drive_path,
            format: kn.divebar.format,
          },
        };
      } else {
        state.selected = {
          source_type: "kn",
          source_ref: kn.youtube_url || kn.url || "",
          song_artist: group.artist,
          song_title: group.title,
          label: `${group.title} — ${group.artist} (${kn.brand_name || kn.brand_code || "online"})`,
          source_meta: { brand_code: kn.brand_code },
        };
      }
    }
    state.step = "confirm"; render();
  }

  function dismissCcExplainer() {
    ccExplainerDismissed = true;
    _markCcExplainerSeen();
    update();
  }

  function toggleExpanded(groupKey) {
    if (expandedSongs.has(groupKey)) expandedSongs.delete(groupKey);
    else expandedSongs.add(groupKey);
    update();
  }

  function renderVersionRow(group, version) {
    let icon, primary, secondary = "", pathBlock = null;
    if (version.source === "local") {
      const local = version.local || {};
      icon = "📁";
      const format = (local.format || "").toUpperCase();
      primary = format
        ? `${local.disc_id || "Local"} — ${format}`
        : (local.disc_id || "Local file");
      secondary = local.filename || "";
      if (local.path) {
        pathBlock = el("details", { class: "sing-version-path-wrap" },
          el("summary", { class: "sing-version-path-summary" }, "show full path"),
          el("div", { class: "sing-version-path" }, local.path),
        );
      }
    } else {
      const kn = version.kn || {};
      const hasDivebar = !!(kn.divebar && kn.divebar.file_id);
      const isCommunity = !!kn.is_community;
      const brand = kn.brand_name || kn.brand_code || "Unknown brand";
      if (hasDivebar) {
        icon = "🎤";
        const format = (kn.divebar.format || "").toUpperCase();
        const quality = kn.divebar.quality ? ` (${kn.divebar.quality})` : "";
        primary = format ? `${brand} — ${format}${quality}` : brand;
        const size = _humanFileSize(kn.divebar.file_size);
        secondary = size ? `via Divebar · ${size}` : "via Divebar";
        if (kn.divebar.drive_path) {
          pathBlock = el("details", { class: "sing-version-path-wrap" },
            el("summary", { class: "sing-version-path-summary" }, "show full path"),
            el("div", { class: "sing-version-path" }, kn.divebar.drive_path),
          );
        }
      } else if (isCommunity) {
        icon = "🧑‍🤝‍🧑";
        primary = brand;
        secondary = "Community · YouTube (download required)";
      } else {
        icon = "🌐";
        primary = brand;
        secondary = "Commercial · YouTube (download required)";
      }
    }
    const card = el("div", { class: "sing-version-card" },
      el("div", { class: "sing-version-icon" }, icon),
      el("div", { class: "sing-version-main" },
        el("div", { class: "sing-version-primary" }, primary),
        secondary ? el("div", { class: "sing-version-secondary" }, secondary) : null,
        pathBlock,
      ),
      el("button", {
        class: "sing-version-pick",
        onclick: (e) => { e.stopPropagation(); if (!armed()) return; pickSpecificVersion(group, version); },
      }, "Pick this version →"),
    );
    return card;
  }

  function renderVersionsExpander(group) {
    const wrapper = el("div", { class: "sing-version-expander" });

    if (!ccExplainerDismissed) {
      wrapper.appendChild(el("div", { class: "sing-cc-explainer" },
        el("div", { class: "sing-cc-title" }, "ℹ️ Commercial vs Community"),
        el("ul", { class: "sing-cc-list" },
          el("li", {}, el("strong", {}, "Commercial"), " — a professional karaoke track: a cover band records the backing, lyrics are on screen, you sing the lead. Sounds like karaoke."),
          el("li", {}, el("strong", {}, "Community"), " — the original recording, with the lead vocal removed by AI. Sounds like the real song."),
        ),
        el("div", { class: "sing-cc-hint" },
          "Most singers pick commercial for classics and community for recent or niche songs."),
        el("button", {
          class: "sing-cc-dismiss",
          onclick: (e) => { e.stopPropagation(); dismissCcExplainer(); },
        }, "Got it"),
      ));
    }

    const sections = [
      { key: "library", label: "In our library" },
      { key: "divebar", label: "Community karaoke (in our library)" },
      { key: "online", label: "Online only (download needed)" },
      { key: "community", label: "Community (AI vocal removal)" },
    ];
    const byKey = { library: [], divebar: [], online: [], community: [] };
    for (const v of (group.versions || [])) byKey[_versionSection(v)].push(v);

    for (const { key, label } of sections) {
      const versions = byKey[key];
      if (!versions.length) continue;
      const section = el("div", { class: "sing-version-section", "data-section": key },
        el("h4", {}, label),
      );
      for (const v of versions) section.appendChild(renderVersionRow(group, v));
      wrapper.appendChild(section);
    }
    return wrapper;
  }

  function renderEmptyStateTriage() {
    // Phase C — three-card triage surfaces when search returns nothing but
    // the singer has clearly tried (query >= 3 chars). Cards are ordered by
    // ascending singer-effort: paste URL (fastest) → ask KJ (variable time,
    // may be declined) → make it yourself on gen.nomadkaraoke.com (fastest
    // for niche songs if the singer is willing to focus on their phone).
    const wrap = el("div", { class: "sing-empty-triage" });

    // Simple KJ Mode — no triage cards. Singer can only pick from search
    // results; if there's nothing, they ask the KJ in person.
    if (state.simpleMode) {
      wrap.appendChild(el("div", { class: "sing-empty-header" },
        el("h3", {}, "We don't have that one."),
        el("p", {}, "Try another search, or talk to the KJ at the front."),
      ));
      return wrap;
    }

    wrap.appendChild(el("div", { class: "sing-empty-header" },
      el("h3", {}, "Can't find it in our catalogue."),
      el("p", {}, "Three ways forward — pick the one that fits how much effort you want."),
    ));

    // Card 1 — paste YouTube link.
    const ytInput = el("input", {
      type: "url",
      placeholder: "https://youtu.be/…",
      class: "sing-empty-input",
    });
    const card1 = el("div", { class: "sing-empty-card" },
      el("h4", {}, "1. Paste a YouTube link"),
      el("p", { class: "sing-empty-desc" },
        "Fastest. If you can find the song on YouTube, paste the link and we'll use that directly. Quality varies."),
      ytInput,
      el("button", {
        class: "btn primary sing-empty-submit",
        onclick: () => {
          const v = (ytInput.value || "").trim();
          if (v) pickYouTube(v);
        },
      }, "Use this YouTube link →"),
    );
    wrap.appendChild(card1);

    // Card 2 — ask KJ to make it tonight. Only when flag is on.
    if (state.makeRequestsEnabled) {
      const mkArtist = el("input", {
        type: "text", placeholder: "Artist",
        class: "sing-empty-input",
        value: state.makeArtist || "",
        oninput: (e) => { state.makeArtist = e.target.value; },
      });
      const mkTitle = el("input", {
        type: "text", placeholder: "Song title",
        class: "sing-empty-input",
        value: state.makeTitle || "",
        oninput: (e) => { state.makeTitle = e.target.value; },
      });
      const card2 = el("div", { class: "sing-empty-card" },
        el("h4", {}, "2. Ask the KJ to make it tonight"),
        el("p", { class: "sing-empty-desc" },
          "Free, but takes time — usually 20 min to 1 hour. The KJ can't always fit it in on a busy night, and some songs are too complex to do live. If they can't do it tonight, they'll let you know."),
        el("label", { class: "sing-empty-label" }, "Artist", mkArtist),
        el("label", { class: "sing-empty-label" }, "Song title", mkTitle),
        el("button", {
          class: "btn primary sing-empty-submit",
          onclick: () => {
            if (!state.makeArtist || !state.makeTitle) return;
            // Confirm dialog surfaces the caveats the singer can't un-know.
            const ok = confirm(
              `Asking the KJ to make "${state.makeTitle}" by ${state.makeArtist} — `
              + `this can take 20 min to 1 hour, or may not be possible tonight. Sure?`
            );
            if (ok) pickMake();
          },
        }, "Ask the KJ →"),
      );
      wrap.appendChild(card2);
    }

    // Card 3 — DIY via gen.nomadkaraoke.com.
    const howDetails = el("details", { class: "sing-empty-howto" },
      el("summary", {}, "How it works (takes ~5 min if you focus)"),
      el("ol", { class: "sing-empty-howto-steps" },
        el("li", {}, "Find your song on YouTube — any version with clear vocals."),
        el("li", {}, "Open ", el("strong", {}, "gen.nomadkaraoke.com"), ", paste the link, pay nothing."),
        el("li", {}, "Wait ~2 min while we separate the vocal from the audio."),
        el("li", {}, "A lyrics review screen appears. Tap any wrong line, fix it, save. This is the focused-phone bit — usually ~3 min."),
        el("li", {}, "We render the karaoke video and publish it to YouTube."),
        el("li", {}, "Copy the new YouTube URL, come back here, paste it into option 1 above."),
      ),
      el("p", { class: "sing-empty-howto-note" },
        "Total time: ~5–10 min if you focus. The KJ does nothing — your song lands in the rotation like any YouTube submission."),
    );
    const card3 = el("div", { class: "sing-empty-card" },
      el("h4", {}, "3. Make it yourself (fastest for niche songs)"),
      el("p", { class: "sing-empty-desc" },
        "If you don't mind some phone time, you can make the karaoke track yourself on gen.nomadkaraoke.com — takes ~5 min if you do the lyrics review, longer if you don't. Then paste the YouTube URL back here."),
      howDetails,
      el("a", {
        href: "https://gen.nomadkaraoke.com",
        target: "_blank",
        rel: "noopener",
        class: "btn primary sing-empty-submit sing-empty-external",
      }, "Open gen.nomadkaraoke.com →"),
    );
    wrap.appendChild(card3);

    return wrap;
  }

  function renderResults() {
    const container = el("div", { class: "results" });
    armAt = Date.now() + armMs();   // freshly-built rows are inert briefly (anti-mis-tap)
    if (loading) container.appendChild(el("p", { class: "hint" }, "Searching…"));
    if (err) container.appendChild(el("p", { class: "error" }, err));

    const songs = results.songs || [];
    // Phase C — genuine empty-state (query was long enough to have searched).
    if (!loading && !err && state.query?.trim().length >= 3 && songs.length === 0) {
      container.appendChild(renderEmptyStateTriage());
    }

    for (const group of songs) {
      const isSingle = group.version_count === 1;
      const isExpanded = expandedSongs.has(group.key);

      // Primary action — stays a big button so a normie in a hurry can tap.
      // Multi-version groups: "Let the KJ pick". Single-version: "Add to queue".
      const ctaLabel = isSingle
        ? "Add to queue"
        : "Let the KJ pick the best version →";
      const onCtaClick = isSingle
        ? () => pickSingleVersion(group)
        : () => pickKjChoice(group);

      const children = [
        el("div", { class: "r-title" }, group.title || ""),
        el("div", { class: "r-sub" }, group.artist || ""),
      ];
      if (group.in_library) {
        children.push(el("span", { class: "badge good" }, "In our library"));
      }
      if (isSingle || !state.simpleMode) {
        children.push(el("button", {
          class: "btn-primary-cta",
          onclick: (e) => { e.stopPropagation(); if (!armed()) return; onCtaClick(); },
        }, ctaLabel));
      }

      // Phase B — only multi-version groups get an expander affordance.
      // In simple mode, the expander is always open (no kj_pick CTA to
      // tuck behind) — the singer's only path is picking a specific version.
      if (!isSingle) {
        if (state.simpleMode) {
          children.push(renderVersionsExpander(group));
        } else {
          const toggleLabel = isExpanded
            ? "Hide versions ↑"
            : `${group.version_count} versions available →`;
          children.push(el("button", {
            class: "sing-versions-toggle",
            "aria-expanded": isExpanded ? "true" : "false",
            onclick: (e) => { e.stopPropagation(); if (!armed()) return; toggleExpanded(group.key); },
          }, toggleLabel));
          if (isExpanded) children.push(renderVersionsExpander(group));
        }
      }

      container.appendChild(el("div", {
        class: "result-row grouped" + (isExpanded ? " expanded" : ""),
      }, ...children));
    }

    return container;
  }

  const card = el("main", { class: "sing-card" },
    el("h2", {}, "Pick your song"),
    el("p", { class: "hint" }, state.simpleMode
      ? `Hi ${state.name.split(/\s+/)[0]} — search for a song below. If we don't have it, just ask the KJ at the front.`
      : `Hi ${state.name.split(/\s+/)[0]} — search for a song below. If we don't have it, you'll get options for how to get it on screen.`),
    el("input", {
      type: "search",
      placeholder: "Type artist or song title…",
      autocomplete: "off",
      oninput: (e) => {
        state.query = e.target.value;
        // Immediate feedback: show the searching hint the moment a real query
        // is typed, before the 700ms debounce elapses (matches the KJ side).
        if (e.target.value.trim().length >= 3 && !loading) {
          loading = true; update();
        }
        doSearch(e.target.value);
      },
      value: state.query,
    }),
    renderResults(),
    // Phase C retired the always-visible <details> fallbacks here — empty
    // search results now render a dedicated 3-card triage (paste URL / ask
    // KJ / DIY via gen) via renderEmptyStateTriage(). A singer with a valid
    // result set doesn't need them as secondary options; if they want them,
    // they can clear the search box and type a nonsense query to reach
    // empty-state.
    el("div", { class: "row" },
      el("button", { class: "btn ghost", onclick: back("identity") }, "Back"),
    ),
  );

  if (state.query) doSearch(state.query);
  return card;
}

function renderConfirm() {
  let submitting = false;
  let err = "";

  function rerender() {
    root.innerHTML = "";
    root.appendChild(renderConfirm());
  }

  const send = async () => {
    if (submitting) return;
    submitting = true; err = "";
    const submitBtn = root.querySelector(".submit-btn");
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Sending…";
    }
    try {
      // Normalise partners — drop rows where the name is blank.
      const cleaned = state.additional
        .map((p) => ({
          name: (p.name || "").trim(),
          phone: (p.phone || "").trim(),
        }))
        .filter((p) => p.name.length > 0);

      // Per-row phone format check — surface the first error inline.
      for (let i = 0; i < cleaned.length; i++) {
        const ph = cleaned[i].phone;
        if (ph && !PHONE_RE.test(ph)) {
          err = `Partner ${i + 1}: phone format looks off.`;
          submitting = false;
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = "Send to KJ";
          }
          const errEl = root.querySelector(".error");
          if (errEl) errEl.textContent = err;
          return;
        }
      }

      const payload = {
        singer_name: state.name,
        phone: state.phone,
        song_artist: state.selected.song_artist || "",
        song_title: state.selected.song_title || "",
        source_type: state.selected.source_type,
        source_ref: state.selected.source_ref,
        source_meta: state.selected.source_meta || null,
      };
      if (cleaned.length > 0) payload.additional_singers = cleaned;

      const data = await submit(payload);
      state.request = data.request;
      // Remember this request id on this device (per token) so the done
      // screen's "your songs tonight" list survives reloads.
      rememberRequestId(TOKEN, data.request.id);
      state.step = "done";
      render();
    } catch (e) {
      err = e.status === 429
        ? "You've submitted a lot — please wait a few minutes."
        : e.data?.error === "simple_mode_disabled_source"
          ? "Song requests are currently restricted. Please refresh this page for the updated options."
          : "Couldn't send — ask the KJ if requests are paused.";
      submitting = false;
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Send to KJ";
      }
      const errEl = root.querySelector(".error");
      if (errEl) errEl.textContent = err;
    }
  };

  function renderPartnersSection() {
    const wrap = el("div", { class: "partners-section" },
      el("div", { class: "partners-title" }, "Singing with anyone else? (optional)"),
    );
    state.additional.forEach((p, i) => {
      wrap.appendChild(el("div", {
        class: "partner-row",
        "data-testid": "partner-row",
      },
        el("input", {
          type: "text",
          placeholder: "Name (e.g. Sarah B.)",
          value: p.name || "",
          "data-testid": `partner-name-${i}`,
          oninput: (e) => { state.additional[i].name = e.target.value; },
        }),
        el("input", {
          type: "tel",
          placeholder: "Phone (optional)",
          value: p.phone || "",
          "data-testid": `partner-phone-${i}`,
          oninput: (e) => { state.additional[i].phone = e.target.value; },
        }),
        el("button", {
          type: "button",
          class: "partner-remove",
          "aria-label": "Remove",
          onclick: () => { state.additional.splice(i, 1); rerender(); },
        }, "×"),
      ));
    });
    if (state.additional.length < MAX_PARTNERS) {
      wrap.appendChild(el("button", {
        type: "button",
        class: "partners-add",
        "data-testid": "add-singer",
        onclick: () => {
          state.additional.push({ name: "", phone: "" });
          rerender();
        },
      }, state.additional.length === 0 ? "+ Add a singer" : "+ Add another singer"));
    } else {
      wrap.appendChild(el("div", { class: "partners-cap-hint" },
        `That's the max — ${MAX_PARTNERS + 1} singers total.`));
    }
    return wrap;
  }

  return el("main", { class: "sing-card" },
    el("h2", {}, "Looking good?"),
    el("div", { class: "pick-summary" },
      el("div", { class: "pick-label" }, state.selected?.label || ""),
    ),
    el("p", { class: "hint" },
      state.phone
        ? `Your details: ${state.name} · ${state.phone}`
        : `Your details: ${state.name}`),
    renderPartnersSection(),
    el("div", { class: "row" },
      el("button", { class: "btn ghost", onclick: back("search") }, "Change"),
      el("button", { class: "btn primary submit-btn", onclick: send }, "Send to KJ"),
    ),
    el("p", { class: "error" }, err),
  );
}

async function fetchMyRequests(ids) {
  if (!ids || !ids.length) {
    return { now_playing: { now_singing: null, up_next: null, queued_count: 0 }, requests: [] };
  }
  const q = ids.join(",");
  const resp = await fetch(
    `${BASE}/my-requests?ids=${encodeURIComponent(q)}&t=${encodeURIComponent(TOKEN)}`,
    { credentials: "same-origin" },
  );
  if (!resp.ok) {
    const err = new Error("my-requests fetch failed");
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}

function _statusLine(item) {
  const req = item.request;
  if (req.status === "rejected") {
    return "The KJ needs to talk to you — see them at the desk.";
  }
  if (req.status === "pending") return "Waiting for KJ to approve…";
  const est = item.estimate;
  if (!est) return "Added to the queue.";
  if (est.now_singing) return "🎤 You're up — break a leg!";
  if (est.position === 1) return "🎤 You're next — head to the mic";
  if (est.position === 2) return "About 1 song to go";
  if (est.position >= 3) {
    const low = Math.round(est.range_low_s / 60);
    const high = Math.round(est.range_high_s / 60);
    return `You're #${est.position} — about ${low}–${high} min`;
  }
  return "Added to the queue.";
}

function _renderSongCard(item) {
  const req = item.request;
  const song = (req.song_title || "") + (req.song_artist ? ` — ${req.song_artist}` : "");
  const partners = req.additional_singers || [];
  const card = el("div", { class: "song-card", "data-status": req.status },
    el("div", { class: "song-card-title" }, song || "(song)"),
    el("div", { class: "song-card-status" }, _statusLine(item)),
  );
  if (partners.length > 0) {
    const names = partners.map((p) => p.name).join(", ");
    card.appendChild(el("div", { class: "song-card-partners" },
      `with ${names}`));
  }
  return card;
}

function renderDone() {
  const card = el("main", { class: "sing-card" },
    renderNowPlaying(),
    el("h2", {}, "Your songs tonight"),
    el("div", { class: "songs-list" }, "Loading your songs…"),
    el("button", {
      class: "btn primary request-another",
      "data-testid": "request-another",
      onclick: () => {
        // Re-sync identity from localStorage in case it was set after module load.
        state.name = LS.get("sing_name") || state.name;
        state.phone = LS.get("sing_phone") || state.phone;
        state.selected = null;
        state.makeArtist = "";
        state.makeTitle = "";
        state.additional = [];
        state.step = "search";
        render();
      },
    }, "+ Request another song"),
    el("div", { id: "push-optin", class: "push-optin" }),
    el("details", { class: "upcoming" },
      el("summary", {}, "Show upcoming singers"),
      el("div", { class: "rotation-body" }, "Open to load…"),
    ),
    el("p", { class: "hint" },
      "Keep this page open — it'll update automatically. Good luck!"),
  );

  setTimeout(maybeShowPushPrompt, 2000);
  pollMyRequests(card);
  // The rotation expander still uses the existing /sing/rotation path.
  const upcoming = card.querySelector(".upcoming");
  upcoming.addEventListener("toggle", async (e) => {
    if (!e.target.open) return;
    if (upcoming.dataset.loaded === "1") return;
    const slot = upcoming.querySelector(".rotation-body");
    if (!slot) return;
    slot.textContent = "Loading…";
    try {
      const payload = await fetchRotation();
      upcoming.dataset.loaded = "1";
      slot.replaceWith(_renderRotationBody({ ...payload, _fetchedAt: Date.now() }));
    } catch {
      slot.textContent = "Couldn't load — try closing and reopening.";
    }
  });
  return card;
}

async function pollMyRequests(card) {
  if (state._statusPollTimer) {
    clearInterval(state._statusPollTimer);
    state._statusPollTimer = null;
  }

  const tick = async () => {
    // Keep only the most-recent stored ids (oldest→newest), then prepend the
    // just-submitted request, staying within the server's MY_REQUESTS_MAX cap.
    let ids = readMyRequestIds(TOKEN).slice(-MY_REQUESTS_MAX);
    if (state.request?.id && !ids.includes(state.request.id)) {
      ids.unshift(state.request.id);
    }
    if (ids.length > MY_REQUESTS_MAX) ids = ids.slice(0, MY_REQUESTS_MAX);
    try {
      const data = await fetchMyRequests(ids);
      onPollSuccess();
      const npNode = card.querySelector(".now-playing");
      if (npNode) updateNowPlaying(npNode, data.now_playing);
      const slot = card.querySelector(".songs-list");
      if (slot) {
        slot.innerHTML = "";
        if (!data.requests.length) {
          slot.appendChild(el("p", { class: "hint" },
            "No songs yet — tap 'Request another song' below."));
        } else {
          for (const item of data.requests) slot.appendChild(_renderSongCard(item));
        }
      }
    } catch {
      onPollFailure();
    }
  };

  tick();
  state._statusPollTimer = setInterval(tick, 15000);
}

// --- Service worker registration ------------------------------------------

async function registerServiceWorker() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return null;
  try {
    const scriptUrl = `${BASE}/sw.js?t=${encodeURIComponent(TOKEN)}`;
    const reg = await navigator.serviceWorker.register(scriptUrl, { scope: `${BASE}/` });
    return reg;
  } catch (e) {
    console.warn("SW registration failed:", e);
    return null;
  }
}

let swRegistration = null;

// --- Push subscription -----------------------------------------------------

function vapidPublicKey() {
  const m = document.querySelector('meta[name="vapid-public-key"]');
  return m ? m.getAttribute("content") : "";
}

function urlB64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function ensurePushSubscription() {
  if (!swRegistration || !("PushManager" in window)) return null;
  const vapidPub = vapidPublicKey();
  if (!vapidPub) return null;
  let sub = await swRegistration.pushManager.getSubscription();
  if (!sub) {
    try {
      sub = await swRegistration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlB64ToUint8Array(vapidPub),
      });
    } catch (e) {
      console.warn("push subscribe failed:", e);
      return null;
    }
  }
  try {
    await fetch(`${BASE}/push/subscribe?t=${encodeURIComponent(TOKEN)}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        phone: state.phone,
        singer_name: state.name,
        subscription: sub.toJSON(),
      }),
    });
    return sub;
  } catch (e) {
    console.warn("push subscribe POST failed:", e);
    return null;
  }
}

async function requestPushPermission() {
  if (!("Notification" in window)) return "unsupported";
  if (Notification.permission === "granted") {
    await ensurePushSubscription();
    return "granted";
  }
  if (Notification.permission === "denied") return "denied";
  const result = await Notification.requestPermission();
  if (result === "granted") await ensurePushSubscription();
  return result;
}

// --- iOS / standalone detection -------------------------------------------

const IS_IOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
const IS_STANDALONE = window.matchMedia("(display-mode: standalone)").matches
  || window.navigator.standalone === true;

// Capture the install prompt event for later use (Android/desktop Chrome).
// Not surfaced in UI for v1 — hook is here in case a future task wires it up.
let deferredInstallPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
});

function maybeShowIosInstructions() {
  if (!IS_IOS || IS_STANDALONE) return false;
  const container = document.getElementById("push-optin");
  if (!container) return true;
  container.innerHTML = "";
  container.classList.add("ios-install");
  container.appendChild(
    el("div", {},
      el("strong", {}, "📱 iPhone? Get tapped when you're up."),
      el("p", {},
        "Tap the Share button, then ",
        el("strong", {}, "Add to Home Screen"),
        ", then reopen from your home screen. You'll then be able to enable notifications."),
      el("button", {
        class: "btn ghost",
        onclick: (e) => { e.target.closest(".push-optin").remove(); },
      }, "Got it"),
    ),
  );
  return true;
}

// Show/hide/update the push-opt-in block based on current Notification.permission.
// Called from renderDone() with a 2s delay so the "you're in!" line registers first.
function maybeShowPushPrompt() {
  const container = document.getElementById("push-optin");
  if (!container) return;
  // iOS Safari outside a standalone PWA can't use Web Push — show instructions instead
  if (maybeShowIosInstructions()) return;
  if (!("Notification" in window) || !swRegistration) {
    container.remove();
    return;
  }
  const perm = Notification.permission;
  if (perm === "granted") {
    ensurePushSubscription();  // idempotent — ensures server row exists for this device
    container.innerHTML = "";
    container.textContent = "✓ Notifications on — we'll buzz you when you're up.";
    container.classList.add("push-on");
    return;
  }
  if (perm === "denied") {
    container.innerHTML = "";
    container.textContent = "Notifications blocked — keep this tab open for updates.";
    container.classList.add("push-blocked");
    return;
  }
  // perm === "default" — show the prompt button
  container.innerHTML = "";
  const btn = el("button", {
    class: "btn primary",
    onclick: async () => {
      btn.disabled = true;
      btn.textContent = "Asking…";
      const result = await requestPushPermission();
      if (result === "granted") {
        container.innerHTML = "";
        container.textContent = "✓ Notifications on — we'll buzz you when you're up.";
        container.classList.add("push-on");
      } else {
        btn.disabled = false;
        btn.textContent = "🔔 Notify me when I'm up";
        if (result === "denied") {
          container.appendChild(el("p", { class: "hint" },
            "You blocked notifications — keep this tab open for updates."));
        }
      }
    },
  }, "🔔 Notify me when I'm up");
  container.appendChild(btn);
}

// --- Persistent rules footer ----------------------------------------------

function renderRulesFooter() {
  const slot = document.getElementById("sing-rules-footer");
  if (!slot) return;
  slot.innerHTML = "";
  slot.appendChild(el("section", { class: "rules-footer" },
    el("h3", {}, "🎤 House rules"),
    el("ul", { class: "rules-short" },
      el("li", {}, "First come, first sing"),
      el("li", {}, "New singers get priority"),
      el("li", {}, "Multiple songs? We'll spread them out"),
      el("li", {}, "Duets welcome — add partners on the confirm screen (up to 3 extras)"),
      el("li", {}, "Need to leave? Ask the KJ"),
      el("li", {}, "♥ = paid priority ($20+)"),
    ),
    el("details", { class: "rules-full" },
      el("summary", {}, "Read the full rules"),
      el("ol", { class: "rules-list" },
        el("li", {},
          el("h4", {}, "First come, first sing"),
          el("p", {}, "The default order is the order you submit your request. "
            + "If Jim, Bob, and Jenny each send in a song, they'll sing in that order."),
        ),
        el("li", {},
          el("h4", {}, "New singers get priority"),
          el("p", {}, "First time singing tonight? You'll get bumped up to sing within "
            + "the next few songs, so everyone gets a chance to perform at least once. "
            + "The next 2 people in line won't be moved — we respect their spot too."),
        ),
        el("li", {},
          el("h4", {}, "Multiple songs welcome"),
          el("p", {}, "Submit as many songs as you want! We'll spread them out in the "
            + "rotation so nobody sings twice in a row."),
        ),
        el("li", {},
          el("h4", {}, "Duets welcome"),
          el("p", {}, "Singing with friends? On the 'Looking good?' screen "
            + "before sending the request, tap '+ Add a singer' to attach "
            + "up to 3 extra people. We'll list everyone on the rotation "
            + "so the KJ knows who to call up. Phone numbers for extras "
            + "are optional — they just help the KJ text them when you're "
            + "close to the front of the queue."),
        ),
        el("li", {},
          el("h4", {}, "Need to leave early?"),
          el("p", {}, "Let the KJ know and we'll try to get you one last song before "
            + "you go. On a busy night when you've already sung 5+ times we may not be "
            + "able to accommodate — but we'll always try."),
        ),
        el("li", {},
          el("h4", {}, "Paid priority ♥"),
          el("p", {}, "Want to skip ahead? Pay $20+ and you'll be bumped up to sing "
            + "very soon. Paid entries are marked with a ♥ on the rotation screen so "
            + "everyone can see it's fair."),
        ),
      ),
    ),
  ));
}

// --- Code-entry mode (no valid token yet) ---------------------------------

function initCodeEntry() {
  const form = document.getElementById("sing-code-form");
  const input = document.getElementById("sing-code-input");
  const errEl = document.getElementById("sing-code-error");
  if (!form || !input || !errEl) return;

  if (codeEntryEl.dataset.badCode) {
    errEl.textContent = "That code didn't match — check with the KJ.";
    errEl.hidden = false;
  }

  async function submitCode(raw) {
    const code = (raw || "").replace(/\D/g, "");
    if (code.length !== 4) {
      errEl.textContent = "Enter the 4-digit code from the screen.";
      errEl.hidden = false;
      return;
    }
    errEl.hidden = true;
    try {
      const resp = await fetch(`${BASE}/validate`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ t: code }),
      });
      if (resp.ok) {
        window.location.href = `${BASE}/?t=${encodeURIComponent(code)}`;
        return;
      }
      errEl.textContent = resp.status === 429
        ? "Too many attempts — please wait a few minutes."
        : "That code didn't match — check the screen again.";
      errEl.hidden = false;
    } catch {
      errEl.textContent = "Couldn't check — are you online?";
      errEl.hidden = false;
    }
  }

  // Auto-submit once a full 4 digits are entered. The form submit handler
  // covers the same path for users who prefer tapping the button.
  input.addEventListener("input", (e) => {
    const cleaned = e.target.value.replace(/\D/g, "").slice(0, 4);
    if (cleaned !== e.target.value) e.target.value = cleaned;
    errEl.hidden = true;
    if (cleaned.length === 4) submitCode(cleaned);
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submitCode(input.value);
  });
}

// Test bridge — only used by Playwright e2e tests. Cheap to leave in
// production: two globals on the window object, no behaviour change.
if (typeof window !== 'undefined') {
  window.__sing_state = state;
  window.__sing_render = render;
}

// --- Bootstrap ------------------------------------------------------------

renderRulesFooter();

if (codeEntryEl) {
  initCodeEntry();
} else if (root) {
  if (INITIAL_REQUEST_ID) {
    const legacyId = parseInt(INITIAL_REQUEST_ID, 10);
    state.request = { id: legacyId };
    // Persist so a legacy ?r=<id> entry survives a "Request another song"
    // — otherwise the next submit overwrites state.request and the original
    // id disappears from the done-screen list.
    rememberRequestId(TOKEN, legacyId);
    state.step = "done";
  }
  // SW + push only make sense in the main SPA path (requires a valid token).
  registerServiceWorker().then((reg) => { swRegistration = reg; });
  render();
}
