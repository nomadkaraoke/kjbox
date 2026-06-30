// preview.js — in-browser preview of any supported file (native video, exotic
// video via cached HLS transcode, CDG zips via canvas, audio, YouTube, divebar
// GCS). Renders entirely in the KJ's browser; never touches the device player.
// Loaded after app.js (uses its escHtml) and cdg.js (uses CDGPlayer).
(function () {
  'use strict';

  var _currentToken = null;
  var _activeMedia = null;   // <video>/<audio> element currently mounted
  var _activeHls = null;     // hls.js instance
  var _cdgRaf = null;        // requestAnimationFrame handle for CDG
  var _hlsLibPromise = null;
  var _previewGen = 0;       // bumped on every open/close; stales out in-flight work

  function byId(id) { return document.getElementById(id); }

  function attrEsc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;')
                    .replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function _extractYouTubeId(url) {
    if (!url) return null;
    var m = String(url).match(
      /(?:youtu\.be\/|youtube\.com\/(?:watch\?(?:.*&)?v=|embed\/|v\/|shorts\/))([A-Za-z0-9_-]{6,})/);
    if (m) return m[1];
    m = String(url).match(/[?&]v=([A-Za-z0-9_-]{6,})/);
    return m ? m[1] : null;
  }

  // --- the one-line button factory (pure; safe to call in HTML-string rows) ---
  function previewButtonHtml(descriptor) {
    // encodeURIComponent leaves ' unescaped; encode it too so the payload can't
    // break out of the single-quoted onclick attribute.
    var enc = encodeURIComponent(JSON.stringify(descriptor || {})).replace(/'/g, '%27');
    return '<button type="button" class="preview-btn" title="Preview playback" ' +
      'onclick="event.stopPropagation();event.preventDefault();openPreviewEnc(\'' +
      enc + '\')">▶</button>';
  }

  function openPreviewEnc(enc) {
    try { openPreview(JSON.parse(decodeURIComponent(enc))); } catch (e) { /* ignore */ }
  }

  function _unavail(reason) {
    return '<div class="preview-unavailable">' +
      (typeof escHtml === 'function' ? escHtml(reason) : attrEsc(reason)) + '</div>';
  }

  function _teardown() {
    if (_cdgRaf) { cancelAnimationFrame(_cdgRaf); _cdgRaf = null; }
    if (_activeHls) { try { _activeHls.destroy(); } catch (e) {} _activeHls = null; }
    if (_activeMedia) {
      try {
        _activeMedia.pause();
        _activeMedia.removeAttribute('src');
        if (_activeMedia.load) _activeMedia.load();
      } catch (e) {}
      _activeMedia = null;
    }
  }

  async function openPreview(descriptor) {
    var gen = ++_previewGen;
    var modal = byId('preview-modal');
    var body = byId('preview-modal-body');
    var titleEl = byId('preview-modal-title');
    var footer = byId('preview-modal-footer');
    if (!modal || !body) return;
    _teardown();
    if (_currentToken) { _postClose(_currentToken); _currentToken = null; }
    modal.classList.remove('hidden');
    if (titleEl) titleEl.textContent = descriptor.title || 'Preview';
    body.innerHTML = '<div class="preview-loading">Loading preview…</div>';
    if (footer) footer.innerHTML = '';

    var res;
    try {
      var r = await fetch('/preview/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(descriptor),
      });
      res = await r.json();
    } catch (e) {
      if (gen !== _previewGen) return;
      body.innerHTML = _unavail('Could not reach the server for preview');
      return;
    }
    if (gen !== _previewGen) {
      // A newer preview opened (or this one was closed) while resolving.
      if (res && res.token) _postClose(res.token);
      return;
    }
    _currentToken = res.token || null;

    // Show file type + extension in the header (e.g. "cdg-zip · .zip").
    if (titleEl && (res.format || res.ext)) {
      var badge = document.createElement('span');
      badge.className = 'preview-type-badge';
      badge.textContent = res.ext ? (res.format + ' · ' + res.ext) : res.format;
      titleEl.appendChild(document.createTextNode(' '));
      titleEl.appendChild(badge);
    }

    switch (res.mode) {
      case 'native_video': _mountVideo(body, '/preview/stream/' + res.token, descriptor); break;
      case 'native_audio': _mountAudio(body, '/preview/stream/' + res.token); break;
      case 'cdg': _mountCdg(body, res.token, gen); break;
      case 'hls': _mountHls(body, res.token, descriptor, gen); break;
      case 'youtube': _mountYouTube(body, res.youtube_url); break;
      default: body.innerHTML = _unavail(res.reason || 'Preview unavailable for this file');
    }
    if (footer) _renderFooter(footer, descriptor);
  }

  function _mountVideo(body, src, descriptor) {
    body.innerHTML = '';
    var v = document.createElement('video');
    v.controls = true; v.autoplay = true; v.playsInline = true; v.src = src;
    v.addEventListener('error', function () {
      // GCS native-passthrough may carry a codec the browser can't decode →
      // fall back to a server transcode.
      if (descriptor.source === 'divebar' && !descriptor.prefer_transcode) {
        var d = Object.assign({}, descriptor, { prefer_transcode: true });
        openPreview(d);
      } else {
        body.innerHTML = _unavail('This file could not be played in the browser');
      }
    });
    body.appendChild(v);
    _activeMedia = v;
  }

  function _mountAudio(body, src) {
    body.innerHTML = '';
    var a = document.createElement('audio');
    a.controls = true; a.autoplay = true; a.src = src;
    body.appendChild(a);
    _activeMedia = a;
  }

  async function _mountCdg(body, token, gen) {
    body.innerHTML = '';
    var canvas = document.createElement('canvas');
    canvas.width = 300; canvas.height = 216; canvas.className = 'preview-cdg-canvas';
    var audio = document.createElement('audio');
    audio.controls = true; audio.autoplay = true; audio.src = '/preview/cdg/' + token + '/audio';
    body.appendChild(canvas);
    body.appendChild(audio);
    _activeMedia = audio;

    var player = null;
    try {
      var resp = await fetch('/preview/cdg/' + token + '/graphics');
      var buf = await resp.arrayBuffer();
      if (gen !== _previewGen || _activeMedia !== audio) return;  // superseded mid-fetch
      if (typeof CDGPlayer === 'function') {
        player = new CDGPlayer(canvas);
        player.load(new Uint8Array(buf));
      }
    } catch (e) { /* graphics failed; audio still plays */ }

    function tick() {
      if (gen !== _previewGen || _activeMedia !== audio) return;  // stop after teardown
      if (player) { try { player.renderAt(audio.currentTime || 0); } catch (e) {} }
      _cdgRaf = requestAnimationFrame(tick);
    }
    tick();
  }

  function _loadHlsLib() {
    if (window.Hls) return Promise.resolve();
    if (_hlsLibPromise) return _hlsLibPromise;
    _hlsLibPromise = new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = '/static/vendor/hls.min.js';
      s.onload = resolve;
      s.onerror = function (err) {
        // Don't cache the rejection — a transient load failure shouldn't break
        // every future HLS preview.
        _hlsLibPromise = null;
        if (s.parentNode) s.parentNode.removeChild(s);
        reject(err);
      };
      document.head.appendChild(s);
    });
    return _hlsLibPromise;
  }

  function _mountHls(body, token, descriptor, gen) {
    body.innerHTML = '';
    var v = document.createElement('video');
    v.controls = true; v.autoplay = true; v.playsInline = true;
    body.appendChild(v);
    _activeMedia = v;
    var url = '/preview/hls/' + token + '/index.m3u8';
    if (v.canPlayType('application/vnd.apple.mpegurl')) { v.src = url; return; }
    _loadHlsLib().then(function () {
      if (gen !== _previewGen || _activeMedia !== v) return;  // superseded during load
      if (window.Hls && window.Hls.isSupported()) {
        var hls = new window.Hls();
        hls.loadSource(url);
        hls.attachMedia(v);
        _activeHls = hls;
      } else {
        body.innerHTML = _unavail('Your browser cannot play this transcoded preview');
      }
    }).catch(function () {
      if (gen !== _previewGen || _activeMedia !== v) return;
      body.innerHTML = _unavail('Could not load the video player');
    });
  }

  function _mountYouTube(body, url) {
    var id = _extractYouTubeId(url);
    if (!id) { body.innerHTML = _unavail('Could not parse the YouTube link'); return; }
    body.innerHTML = '<iframe class="preview-yt" src="https://www.youtube.com/embed/' +
      attrEsc(id) + '?autoplay=1" frameborder="0" ' +
      'allow="autoplay; encrypted-media" allowfullscreen></iframe>';
  }

  function _renderFooter(footer, descriptor) {
    footer.innerHTML = '';
    // Reuse the rotation row's existing link flow (selectRotSearchResult over the
    // live rotSearchResults array) so auditioning → linking is one click.
    var canLink = descriptor.link_idx != null
      && typeof selectRotSearchResult === 'function'
      && typeof rotSearchResults !== 'undefined'
      && rotSearchResults[descriptor.link_idx];
    if (canLink) {
      var btn = document.createElement('button');
      btn.className = 'preview-link-btn';
      btn.textContent = descriptor.link_label || 'Use this version';
      btn.onclick = function () {
        try { selectRotSearchResult(rotSearchResults[descriptor.link_idx]); } catch (e) {}
        closePreview();
      };
      footer.appendChild(btn);
    }
  }

  function _postClose(token) {
    try {
      fetch('/preview/close', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: token }),
      }).catch(function () {});
    } catch (e) {}
  }

  function closePreview() {
    ++_previewGen;  // invalidate any in-flight resolve / async mount
    _teardown();
    var modal = byId('preview-modal');
    if (modal) modal.classList.add('hidden');
    var body = byId('preview-modal-body');
    if (body) body.innerHTML = '';
    if (_currentToken) { _postClose(_currentToken); _currentToken = null; }
  }

  // Escape key closes the modal when visible.
  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        var modal = byId('preview-modal');
        if (modal && !modal.classList.contains('hidden')) closePreview();
      }
    });
  }

  // Browser globals.
  if (typeof window !== 'undefined') {
    window.openPreview = openPreview;
    window.openPreviewEnc = openPreviewEnc;
    window.previewButtonHtml = previewButtonHtml;
    window.closePreview = closePreview;
  }
  // Node test exports (pure helpers only).
  if (typeof module === 'object' && module.exports) {
    module.exports = { previewButtonHtml: previewButtonHtml, _extractYouTubeId: _extractYouTubeId };
  }
})();
