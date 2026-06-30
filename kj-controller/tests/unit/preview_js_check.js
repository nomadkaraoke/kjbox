// Standalone node harness for preview.js pure helpers.
const path = require('path');
const mod = require(path.join(__dirname, '..', '..', 'static', 'preview.js'));
const { previewButtonHtml, _extractYouTubeId } = mod;

function fail(m) { console.error('FAIL: ' + m); process.exit(1); }

// Button factory round-trips the descriptor via URL-encoded JSON.
const html = previewButtonHtml({ source: 'local', file_path: "/a/b c'd.mp4" });
if (html.indexOf('preview-btn') < 0) fail('missing class: ' + html);
if (html.indexOf('▶') < 0) fail('missing play glyph');
const m = html.match(/openPreviewEnc\('([^']*)'\)/);
if (!m) fail('no openPreviewEnc payload: ' + html);
const round = JSON.parse(decodeURIComponent(m[1]));
if (round.file_path !== "/a/b c'd.mp4") fail('descriptor did not round-trip: ' + round.file_path);
// Single quote in the path must be percent-encoded so it can't break the attribute.
if (m[1].indexOf("'") >= 0) fail('unescaped single quote in onclick payload');

// YouTube id extraction across URL shapes.
const cases = {
  'https://youtu.be/dQw4w9WgXcQ': 'dQw4w9WgXcQ',
  'https://www.youtube.com/watch?v=dQw4w9WgXcQ': 'dQw4w9WgXcQ',
  'https://www.youtube.com/embed/dQw4w9WgXcQ': 'dQw4w9WgXcQ',
  'https://m.youtube.com/watch?feature=x&v=dQw4w9WgXcQ': 'dQw4w9WgXcQ',
};
for (const url in cases) {
  if (_extractYouTubeId(url) !== cases[url]) fail('yt id for ' + url + ' => ' + _extractYouTubeId(url));
}
if (_extractYouTubeId('https://example.com/nope') !== null) fail('expected null for non-yt url');

console.log('OK');
