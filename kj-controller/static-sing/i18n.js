// Nomad Karaoke — singer UI i18n runtime.
// Vanilla ES module, no build step. Mirrors the karaoke-gen pipeline:
// `messages/en.json` is the source of truth; `scripts/translate.py` produces
// the other locale files (Gemini two-pass + shared GCS cache), CI checks key
// parity. This module only *loads* them.
//
// API:
//   await initI18n(base, version)  — pick a locale, fetch messages, set <html lang/dir>
//   t("a.b.c", {vars})             — translate with {placeholder} interpolation
//   tn("a.b", count, {vars})       — plural: picks key.one / key.other via Intl.PluralRules
//   getLocale() / setLocale(code)  — current + persisted switch (re-fetches messages)
//   applyStaticStrings(root)       — fills data-i18n / data-i18n-placeholder / -aria-label
//   LOCALES / RTL_LOCALES / detectLocale()

export const LOCALES = {
  en: "English",
  ar: "العربية",
  ca: "Català",
  cs: "Čeština",
  da: "Dansk",
  de: "Deutsch",
  el: "Ελληνικά",
  es: "Español",
  fi: "Suomi",
  fr: "Français",
  he: "עברית",
  hi: "हिन्दी",
  hr: "Hrvatski",
  hu: "Magyar",
  id: "Bahasa Indonesia",
  it: "Italiano",
  ja: "日本語",
  ko: "한국어",
  ms: "Bahasa Melayu",
  nb: "Norsk",
  nl: "Nederlands",
  pl: "Polski",
  pt: "Português",
  ro: "Română",
  ru: "Русский",
  sk: "Slovenčina",
  sv: "Svenska",
  th: "ไทย",
  tl: "Filipino",
  tr: "Türkçe",
  uk: "Українська",
  vi: "Tiếng Việt",
  zh: "中文",
};

export const RTL_LOCALES = new Set(["ar", "he"]);

// Browser language tags that don't map 1:1 onto our locale codes.
const ALIASES = { no: "nb", nn: "nb", fil: "tl", iw: "he", in: "id", "zh-hans": "zh", "zh-hant": "zh" };

const LS_KEY = "sing_lang";

let _base = "";
let _version = "";
let _locale = "en";
let _messages = {};   // active locale (may be en)
let _en = {};         // always-loaded fallback
let _plural = null;

function _lsGet(k) { try { return localStorage.getItem(k) || ""; } catch { return ""; } }
function _lsSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }

export function normalizeLocale(tag) {
  const raw = String(tag || "").trim().toLowerCase();
  if (!raw) return "";
  if (ALIASES[raw]) return ALIASES[raw];
  if (LOCALES[raw]) return raw;
  const base = raw.split(/[-_]/)[0];
  if (ALIASES[base]) return ALIASES[base];
  return LOCALES[base] ? base : "";
}

// Priority: ?lang= (explicit, also persisted) → saved choice → browser languages → en.
export function detectLocale() {
  let fromQuery = "";
  try { fromQuery = new URLSearchParams(window.location.search).get("lang") || ""; } catch { /* no window */ }
  const q = normalizeLocale(fromQuery);
  if (q) { _lsSet(LS_KEY, q); return q; }
  const saved = normalizeLocale(_lsGet(LS_KEY));
  if (saved) return saved;
  const langs = (typeof navigator !== "undefined" && navigator.languages && navigator.languages.length)
    ? navigator.languages
    : [typeof navigator !== "undefined" ? navigator.language : ""];
  for (const l of langs) {
    const n = normalizeLocale(l);
    if (n) return n;
  }
  return "en";
}

async function _fetchMessages(locale) {
  const v = _version ? `?v=${encodeURIComponent(_version)}` : "";
  const resp = await fetch(`${_base}/static/messages/${locale}.json${v}`, { credentials: "same-origin" });
  if (!resp.ok) throw new Error(`messages ${locale}: ${resp.status}`);
  return resp.json();
}

function _applyDocumentAttrs() {
  const html = document.documentElement;
  html.setAttribute("lang", _locale);
  html.setAttribute("dir", RTL_LOCALES.has(_locale) ? "rtl" : "ltr");
  const title = t("meta.title");
  if (title && title !== "meta.title") document.title = title;
}

export async function initI18n(base, version) {
  _base = base || "";
  _version = version || "";
  const want = detectLocale();
  try {
    _en = await _fetchMessages("en");
  } catch (e) {
    console.warn("i18n: en.json failed to load", e);
    _en = {};
  }
  await _activate(want);
}

async function _activate(locale) {
  if (locale === "en" || !LOCALES[locale]) {
    _locale = "en";
    _messages = _en;
  } else {
    try {
      _messages = await _fetchMessages(locale);
      _locale = locale;
    } catch (e) {
      console.warn(`i18n: ${locale}.json failed to load — falling back to English`, e);
      _locale = "en";
      _messages = _en;
    }
  }
  try { _plural = new Intl.PluralRules(_locale); } catch { _plural = null; }
  if (typeof document !== "undefined") {
    _applyDocumentAttrs();
    applyStaticStrings(document);
  }
}

export function getLocale() { return _locale; }

export function localeName(code) { return LOCALES[code] || code; }

export async function setLocale(locale) {
  const n = normalizeLocale(locale) || "en";
  _lsSet(LS_KEY, n);
  await _activate(n);
  return _locale;
}

function _lookup(obj, key) {
  let cur = obj;
  for (const part of key.split(".")) {
    if (cur == null || typeof cur !== "object" || !(part in cur)) return undefined;
    cur = cur[part];
  }
  return cur;
}

function _interpolate(str, vars) {
  if (!vars) return str;
  return str.replace(/\{(\w+)\}/g, (m, k) => (k in vars && vars[k] != null ? String(vars[k]) : m));
}

// Translate a leaf key. Falls back to English, then to the key itself (so a
// missing string is visible rather than blank).
export function t(key, vars) {
  let val = _lookup(_messages, key);
  if (typeof val !== "string") val = _lookup(_en, key);
  if (typeof val !== "string") return key;
  return _interpolate(val, vars);
}

// Plural-aware translate: `key` points at an object with CLDR category keys
// ({one, other, few, many, zero, two}); `count` is exposed as {count}.
export function tn(key, count, vars) {
  const n = Number(count) || 0;
  let forms = _lookup(_messages, key);
  if (!forms || typeof forms !== "object") forms = _lookup(_en, key);
  if (!forms || typeof forms !== "object") return t(key, { count: n, ...(vars || {}) });
  const cat = _plural ? _plural.select(n) : (n === 1 ? "one" : "other");
  const str = forms[cat] || forms.other || Object.values(forms)[0] || key;
  return _interpolate(String(str), { count: n, ...(vars || {}) });
}

// Fill server-rendered markup: <el data-i18n="key">, data-i18n-placeholder,
// data-i18n-aria-label, data-i18n-title. Idempotent — safe to re-run on a
// language switch.
export function applyStaticStrings(root) {
  const scope = root || document;
  scope.querySelectorAll("[data-i18n]").forEach((el) => {
    const v = t(el.getAttribute("data-i18n"));
    if (v && v !== el.getAttribute("data-i18n")) el.textContent = v;
  });
  for (const [attr, dataAttr] of [["placeholder", "data-i18n-placeholder"],
                                  ["aria-label", "data-i18n-aria-label"],
                                  ["title", "data-i18n-title"]]) {
    scope.querySelectorAll(`[${dataAttr}]`).forEach((el) => {
      const k = el.getAttribute(dataAttr);
      const v = t(k);
      if (v && v !== k) el.setAttribute(attr, v);
    });
  }
}
