// Nomad Karaoke — singer-facing request SPA.
// Vanilla JS, no framework, no build step. Mobile-first.

const root = document.getElementById("sing-root");
if (!root) {
  // Template rendered the "closed" variant which has no #sing-root — this
  // script shouldn't load in that case, but guard in case of future template
  // changes so we don't throw on .dataset access.
  throw new Error("sing-root element missing — requests are closed");
}
const TOKEN = root.dataset.token;
const INITIAL_REQUEST_ID = root.dataset.requestId;

const LS = {
  get: (k) => { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};

const PHONE_RE = /^\+?[0-9 \-()]{7,20}$/;

const state = {
  step: "landing",
  name: LS.get("sing_name"),
  phone: LS.get("sing_phone"),
  query: "",
  selected: null,   // { source_type, source_ref, song_artist, song_title, label }
  makeArtist: "",
  makeTitle: "",
  request: null,    // after submit
  status: null,     // /sing/status response
};

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
  return fetchJson(`/sing/search?q=${q}`);
}

async function submit(payload) {
  return fetchJson("/sing/submit", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

async function fetchStatus(id) {
  // Status now requires the same token gate as other sing routes — the
  // backend cross-references the request's stored token to prevent cross-
  // event reads, so we must pass ours on every poll.
  const t = encodeURIComponent(TOKEN);
  const resp = await fetch(`/sing/status/${id}?t=${t}`, { credentials: "same-origin" });
  if (!resp.ok) throw new Error("status fetch failed");
  return resp.json();
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
      const resp = await fetch(`/sing/now?t=${encodeURIComponent(TOKEN)}`, {
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

function renderLanding() {
  return el("main", { class: "sing-card" },
    renderNowPlaying(),   // Task 5 populates this; stub is harmless
    el("h1", {}, "Request a song"),
    el("p", {},
      "Tap below to add your song to the rotation. The KJ will call you up when you're on."),
    el("button", {
      class: "btn primary",
      onclick: () => {
        state.step = state.name && state.phone && PHONE_RE.test(state.phone)
          ? "search"
          : "identity";
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
    el("p", { class: "sing-footer-links" },
      el("a", { href: "/sing/rules", target: "_blank" }, "House rules"),
    ),
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
    if (!PHONE_RE.test(draft.phone.trim())) {
      draft.err = "Please enter a valid phone number (digits, spaces, or + allowed).";
      rerender(); return;
    }
    state.name = draft.name.trim();
    state.phone = draft.phone.trim();
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
    el("p", { class: "hint" },
      "The KJ uses your phone number to tell you apart from other singers with the same first name. It's not shared with anyone else."),
    el("form", { onsubmit: onSubmit },
      el("label", {}, "Your name",
        el("input", {
          type: "text", autocomplete: "given-name",
          value: draft.name, placeholder: "e.g. Andrew",
          oninput: (e) => { draft.name = e.target.value; },
        }),
      ),
      el("label", {}, "Phone number",
        el("input", {
          type: "tel", autocomplete: "tel",
          value: draft.phone, placeholder: "+61 400 123 456",
          oninput: (e) => { draft.phone = e.target.value; },
        }),
      ),
      draft.err ? el("p", { class: "error" }, draft.err) : null,
      el("div", { class: "row" },
        el("button", { type: "button", class: "btn ghost", onclick: () => { state._identityDraft = null; state.step = "landing"; render(); } }, "Back"),
        el("button", { type: "submit", class: "btn primary" }, "Next"),
      ),
    ),
  );
}

function renderSearch() {
  let results = { local: [], karaoke_nerds: [] };
  let loading = false;
  let err = "";

  let debounceTimer = null;
  const doSearch = (q) => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(async () => {
      if (q.trim().length < 3) { results = { local: [], karaoke_nerds: [] }; update(); return; }
      loading = true; err = ""; update();
      try {
        const data = await search(q.trim());
        results = data;
      } catch (e) {
        err = "Search failed. Try again.";
      } finally {
        loading = false; update();
      }
    }, 300);
  };

  const pickLocal = (r) => {
    state.selected = {
      source_type: "local",
      source_ref: r.path,
      song_artist: r.artist || "",
      song_title: r.title || "",
      label: `${r.title || r.filename} — ${r.artist || ""} (in library)`,
    };
    state.step = "confirm"; render();
  };

  const pickKN = (song, track) => {
    const youtubeUrl = track.youtube_url || track.url || "";
    const hasDivebar = !!(track.divebar && track.divebar.file_id);
    state.selected = hasDivebar
      ? {
          source_type: "divebar",
          source_ref: track.divebar.file_id,
          song_artist: song.artist,
          song_title: song.title,
          label: `${song.title} — ${song.artist} (community karaoke)`,
          source_meta: { brand_code: track.brand_code, disc_id: track.disc_id },
        }
      : {
          source_type: "kn",
          source_ref: youtubeUrl,
          song_artist: song.artist,
          song_title: song.title,
          label: `${song.title} — ${song.artist} (online karaoke)`,
          source_meta: { brand_code: track.brand_code, disc_id: track.disc_id },
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

  function renderResults() {
    const container = el("div", { class: "results" });
    if (loading) container.appendChild(el("p", { class: "hint" }, "Searching…"));
    if (err) container.appendChild(el("p", { class: "error" }, err));

    if (results.local?.length) {
      container.appendChild(el("h3", {}, "In our library"));
      for (const r of results.local) {
        container.appendChild(el("button", {
          class: "result-row",
          onclick: () => pickLocal(r),
        },
          el("div", { class: "r-title" }, r.title || r.filename),
          el("div", { class: "r-sub" }, r.artist || ""),
          el("span", { class: "badge good" }, "Good to go"),
        ));
      }
    }

    if (results.karaoke_nerds?.length) {
      container.appendChild(el("h3", {}, "Community karaoke"));
      for (const song of results.karaoke_nerds) {
        for (const track of song.tracks || []) {
          const hasDivebar = !!(track.divebar && track.divebar.file_id);
          container.appendChild(el("button", {
            class: "result-row",
            onclick: () => pickKN(song, track),
          },
            el("div", { class: "r-title" }, song.title),
            el("div", { class: "r-sub" }, `${song.artist} — ${track.brand_code || ""}`),
            el("span", { class: "badge " + (hasDivebar ? "good" : "warn") },
                hasDivebar ? "Good to go" : "Download needed"),
          ));
        }
      }
    }

    return container;
  }

  const card = el("main", { class: "sing-card" },
    el("h2", {}, "Pick your song"),
    el("p", { class: "hint" }, `Hi ${state.name.split(/\s+/)[0]} — find your song below, or paste a YouTube link / ask the KJ to make one.`),
    el("input", {
      type: "search",
      placeholder: "Type artist or song title…",
      autocomplete: "off",
      oninput: (e) => { state.query = e.target.value; doSearch(e.target.value); },
      value: state.query,
    }),
    renderResults(),
    el("details", { class: "fallback" },
      el("summary", {}, "Paste a YouTube link"),
      el("input", {
        type: "url", placeholder: "https://youtu.be/…",
        onchange: (e) => { const v = e.target.value.trim(); if (v) pickYouTube(v); },
      }),
    ),
    el("details", { class: "fallback" },
      el("summary", {}, "Ask the KJ to make this one"),
      el("input", { type: "text", placeholder: "Artist",
        oninput: (e) => { state.makeArtist = e.target.value; } }),
      el("input", { type: "text", placeholder: "Song title",
        oninput: (e) => { state.makeTitle = e.target.value; } }),
      el("button", { class: "btn primary",
        onclick: () => { if (state.makeArtist && state.makeTitle) pickMake(); } },
        "Send make request"),
    ),
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
  const send = async () => {
    if (submitting) return;
    submitting = true; err = "";
    root.querySelector(".submit-btn").disabled = true;
    root.querySelector(".submit-btn").textContent = "Sending…";
    try {
      const payload = {
        singer_name: state.name,
        phone: state.phone,
        song_artist: state.selected.song_artist || "",
        song_title: state.selected.song_title || "",
        source_type: state.selected.source_type,
        source_ref: state.selected.source_ref,
        source_meta: state.selected.source_meta || null,
      };
      const data = await submit(payload);
      state.request = data.request;
      state.step = "done";
      render();
    } catch (e) {
      err = e.status === 429
        ? "You've submitted a lot — please wait a few minutes."
        : "Couldn't send — ask the KJ if requests are paused.";
      submitting = false;
      root.querySelector(".submit-btn").disabled = false;
      root.querySelector(".submit-btn").textContent = "Send to KJ";
      const errEl = root.querySelector(".error");
      if (errEl) errEl.textContent = err;
    }
  };

  return el("main", { class: "sing-card" },
    el("h2", {}, "Looking good?"),
    el("div", { class: "pick-summary" },
      el("div", { class: "pick-label" }, state.selected?.label || ""),
    ),
    el("p", { class: "hint" },
      `Your details: ${state.name} · ${state.phone}`),
    el("div", { class: "row" },
      el("button", { class: "btn ghost", onclick: back("search") }, "Change"),
      el("button", { class: "btn primary submit-btn", onclick: send }, "Send to KJ"),
    ),
    el("p", { class: "error" }, err),
  );
}

function renderDone() {
  const card = el("main", { class: "sing-card" },
    renderNowPlaying(),
    el("h2", {}, state.request?.status === "approved" ? "You're in!" : "Sent!"),
    el("p", {}, state.request?.status === "approved"
      ? "The KJ has added you to the queue."
      : "The KJ will look at it and add you to the queue."),
    el("div", { class: "status-live" }, "Checking your position…"),
    el("div", { id: "push-optin", class: "push-optin" }),
    el("details", { class: "upcoming" },
      el("summary", {}, "Show upcoming singers"),
      el("div", { class: "queue-list" }, "Loading…"),
    ),
    el("details", { class: "rules-inline" },
      el("summary", {}, "🎤 House rules"),
      el("ul", { class: "rules-short" },
        el("li", {}, "First come, first sing"),
        el("li", {}, "New singers get priority"),
        el("li", {}, "Multiple songs? We'll spread them out"),
        el("li", {}, "Need to leave? Ask the KJ"),
        el("li", {}, "♥ = paid priority ($20+)"),
      ),
      el("p", {},
        el("a", { href: "/sing/rules", target: "_blank" }, "Read full rules →")),
    ),
    el("p", { class: "hint" },
      "Keep this page open — it'll update automatically. Good luck!"),
  );

  setTimeout(maybeShowPushPrompt, 2000);
  pollStatus(card);
  return card;
}

async function pollStatus(card) {
  const live = card.querySelector(".status-live");
  const queueEl = card.querySelector(".queue-list");
  const reqId = state.request?.id;
  if (!reqId || !live) return;

  // Clear any previous poll timer so re-rendering the "done" step doesn't
  // leak overlapping intervals.
  if (state._statusPollTimer) {
    clearInterval(state._statusPollTimer);
    state._statusPollTimer = null;
  }

  const tick = async () => {
    try {
      const data = await fetchStatus(reqId);
      onPollSuccess();
      state.status = data;
      const est = data.estimate;
      if (est && est.now_singing) {
        live.textContent = "🎤 You're up — break a leg!";
      } else if (est && est.position === 1) {
        live.textContent = "🎤 You're next — head to the mic";
      } else if (est && est.position === 2) {
        live.textContent = "About 1 song to go";
      } else if (est && est.position >= 3) {
        const low = Math.round(est.range_low_s / 60);
        const high = Math.round(est.range_high_s / 60);
        live.textContent = `You're #${est.position} — about ${low}–${high} min`;
      } else if (data.request?.status === "pending") {
        live.textContent = "Waiting for KJ to approve…";
      } else if (data.request?.status === "rejected") {
        live.textContent = "The KJ needs to talk to you — see them at the desk.";
      } else {
        live.textContent = "Added to the queue.";
      }
      if (queueEl && data.queue) {
        queueEl.innerHTML = "";
        for (const entry of data.queue) {
          queueEl.appendChild(el("div", { class: "queue-row" },
            el("span", { class: "q-name" }, entry.first_name || "—"),
            el("span", { class: "q-song" }, entry.song_artist || ""),
            el("span", { class: "q-status" }, entry.status || ""),
          ));
        }
      }
      if (data.now_playing) {
        const npNode = card.querySelector(".now-playing");
        if (npNode) updateNowPlaying(npNode, data.now_playing);
      }
    } catch {
      onPollFailure();
      live.textContent = "Couldn't update — checking again in a moment.";
    }
  };

  tick();
  state._statusPollTimer = setInterval(tick, 15000);
}

// --- Bootstrap -------------------------------------------------------------

if (INITIAL_REQUEST_ID) {
  state.request = { id: parseInt(INITIAL_REQUEST_ID, 10) };
  state.step = "done";
}

// --- Service worker registration ------------------------------------------

async function registerServiceWorker() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return null;
  try {
    const scriptUrl = `/sing/sw.js?t=${encodeURIComponent(TOKEN)}`;
    const reg = await navigator.serviceWorker.register(scriptUrl, { scope: "/sing/" });
    return reg;
  } catch (e) {
    console.warn("SW registration failed:", e);
    return null;
  }
}

let swRegistration = null;
registerServiceWorker().then((reg) => { swRegistration = reg; });

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
    await fetch(`/sing/push/subscribe?t=${encodeURIComponent(TOKEN)}`, {
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

// Show/hide/update the push-opt-in block based on current Notification.permission.
// Called from renderDone() with a 2s delay so the "you're in!" line registers first.
function maybeShowPushPrompt() {
  const container = document.getElementById("push-optin");
  if (!container) return;
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

render();
