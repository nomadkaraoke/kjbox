// Mirror of text_normalize.py. Keep in lockstep; parity is enforced by
// tests/unit/test_text_normalize_js_parity.py (runs this file under node).
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.TextNormalize = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {

  function makeNormalizer(maps) {
    const latin = maps.latinSpecialMap || {};
    const abbrev = maps.abbrevMap || {};
    const numberWords = maps.numberWords || {};   // word -> int
    const roman = maps.romanMap || {};            // roman -> int
    const tens = new Set([20, 30, 40, 50, 60, 70, 80, 90]);

    const latinKeys = Object.keys(latin);
    const latinRe = latinKeys.length
      ? new RegExp('[' + latinKeys.join('').replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ']', 'g')
      : null;
    // Unicode-aware whole-word boundary (JS \b is ASCII-only: "Жft" would still match).
    // Search flavour of text_normalize.normalize: drop only the feat word, keep
    // the featured artist (identity_normalize's clause strip is Python-only).
    const featRe = /(?<![\p{L}\p{N}_])(?:feat\.?|ft\.?|featuring)(?=\s)/igu;
    const symbolAndRe = /\s*[&+]\s*/g;
    const aposRe = /['\u2018\u2019\u02BC`]+/gu;
    // NOTE: JS \w is ASCII-only (unlike Python re.UNICODE), so non-Latin
    // scripts (Cyrillic/Greek/CJK) differ from the Python normalizer. Accepted:
    // the karaoke catalog is effectively all Latin-script.
    const punctRe = /[^\w\s]/gu;

    function canonNumbers(toks) {
      const out = [];
      for (let i = 0; i < toks.length; i++) {
        const t = toks[i];
        let val = (t in numberWords) ? numberWords[t]
                : (t in roman) ? roman[t] : null;
        if (val === null) { out.push(t); continue; }
        if (tens.has(val) && i + 1 < toks.length && (toks[i + 1] in numberWords)) {
          const ones = numberWords[toks[i + 1]];
          if (ones >= 1 && ones <= 9) { val += ones; i++; }
        }
        out.push(String(val));
      }
      return out;
    }

    function normalize(text) {
      if (!text) return '';
      let s = text.normalize('NFD').replace(/[̀-ͯ]/g, '');
      if (latinRe) s = s.replace(latinRe, m => latin[m]);
      s = s.toLowerCase();
      s = s.replace(featRe, ' ');
      s = s.replace(symbolAndRe, ' and ');
      s = s.replace(aposRe, '');
      s = s.replace(punctRe, ' ');
      let toks = s.split(/\s+/).filter(Boolean);
      toks = toks.map(t => (t in abbrev) ? abbrev[t] : t);
      toks = canonNumbers(toks);
      return toks.join(' ');
    }

    function tokens(text) {
      const n = normalize(text);
      return n ? n.split(' ') : [];
    }

    // Mirror of text_normalize.joined_forms / index_text: the INDEXED side of a
    // search also carries each adjacent token pair joined ("over thinking" ->
    // "overthinking"). Never apply to the query.
    function joinedForms(normText) {
      const toks = normText ? normText.split(' ') : [];
      const seen = new Set(toks);
      const out = [];
      for (let i = 0; i + 1 < toks.length; i++) {
        const j = toks[i] + toks[i + 1];
        if (!seen.has(j)) { seen.add(j); out.push(j); }
      }
      return out;
    }

    function indexText(text) {
      const n = normalize(text);
      const extra = joinedForms(n);
      return extra.length ? n + ' ' + extra.join(' ') : n;
    }

    return { normalize, tokens, joinedForms, indexText };
  }

  return { makeNormalizer };
}));
