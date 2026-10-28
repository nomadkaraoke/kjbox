/* night-clips review page. Vanilla JS, no build step. */
(() => {
  'use strict';

  const IG_CAPTION_MAX = 2200;
  const $ = (id) => document.getElementById(id);

  let S = null;              // last /api/state payload
  let selected = [];         // ordered clip ids
  let captionDirty = false;  // caption edited by hand -> don't auto-regenerate
  let playing = null;        // clip id with an inline player

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  async function api(path, body) {
    const opts = body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    };
    const r = await fetch(path, opts);
    let data = {};
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok && data.ok === undefined) data.ok = false;
    return data;
  }

  function setStatus(msg, kind) {
    const el = $('status');
    el.textContent = msg || '';
    el.className = 'status' + (kind ? ' ' + kind : '');
  }

  const byId = () => Object.fromEntries(S.clips.map((c) => [c.id, c]));
  const hhmm = (ts) => (ts || '').slice(11, 16);
  const hhmmss = (ts) => (ts || '').slice(11, 19);
  const fmtDur = (s) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}` : '');
  const songOf = (c) => (c.song_idx === null || c.song_idx === undefined ? null : S.songs[c.song_idx]);

  function prettyNight(n) {
    const d = new Date(n + 'T12:00:00');
    return d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
  }

  const REASONS = {
    song_override_singer: 'singer set for this song',
    song_override_kj: 'KJ set for this song',
    night_default: 'singer’s answer for the night',
    unknown: 'never answered',
  };
  const FLAGS = {
    near_change: 'clip starts within 15s of a song change',
    stale_song: 'song started over 8 min before the clip',
    after_song_end: 'clip starts after this song was marked done',
  };
  const STATUS_LABEL = {
    eligible: 'OK to post', unknown: 'Consent unknown', excluded: 'Said no', unassigned: 'No song matched',
  };

  // ---------------------------------------------------------------- render
  function render() {
    $('title').textContent = prettyNight(S.night);
    const src = { events: 'per-song consent log', legacy: 'legacy night consent', unavailable: 'NO consent data' };
    $('source').textContent = `Consent: ${src[S.consent_source] || S.consent_source}`;
    renderBanners();
    renderGrid();
    renderPanel();
  }

  function renderBanners() {
    const b = $('banners');
    b.replaceChildren();
    if (S.warning) b.append(h('div', { class: 'banner danger' }, '⚠ ', S.warning));
    if (S.posted) {
      b.append(h('div', { class: 'banner warn' },
        `Already marked as posted at ${S.posted.posted_at} (${(S.posted.files || []).length} clips). Publishing again will ask you to confirm.`));
    }
    if (S.draft_loaded) b.append(h('div', { class: 'banner info' }, 'Restored your saved draft.'));
  }

  function renderGrid() {
    const showExcluded = $('show-excluded').checked;
    const selOnly = $('selected-only').checked;
    const counts = { eligible: 0, unknown: 0, excluded: 0, unassigned: 0 };
    S.clips.forEach((c) => { counts[c.status] += 1; });
    $('counts').textContent = `${S.clips.length} clips · ${counts.eligible} OK · ${counts.unknown} unknown · ` +
      `${counts.excluded} excluded · ${counts.unassigned} unmatched`;

    const grid = $('grid');
    grid.replaceChildren();
    for (const c of S.clips) {
      if (c.status === 'excluded' && !showExcluded && !selected.includes(c.id)) continue;
      if (selOnly && !selected.includes(c.id)) continue;
      grid.append(card(c));
    }
    if (!grid.children.length) grid.append(h('p', { class: 'muted' }, 'No clips to show.'));
  }

  const isLong = (c) => c.duration && c.duration > S.ig_max_s;
  const cutEnd = (c) => (c.trim_start || 0) + S.ig_max_s;

  // Plays only the part Instagram will get: starts at the chosen start, stops at start + 59s.
  function previewVideo(c) {
    const v = h('video', { src: `/media/${c.id}`, controls: true, autoplay: true, playsinline: true, preload: 'auto' });
    if (isLong(c)) {
      v.dataset.clip = c.id;
      v.addEventListener('loadedmetadata', () => { v.currentTime = c.trim_start || 0; });
      v.addEventListener('timeupdate', () => {
        if (v.currentTime >= cutEnd(c)) { v.pause(); v.currentTime = c.trim_start || 0; }
      });
    }
    return v;
  }

  function trimControl(c) {
    const max = Math.max(0, c.duration - S.ig_max_s);
    const label = h('span', { class: 'trim-lbl' });
    const show = (s) => {
      label.textContent = `Cut starts ${fmtDur(s)} · ends ${fmtDur(Math.min(c.duration, s + S.ig_max_s))} (of ${fmtDur(c.duration)})`;
    };
    show(c.trim_start || 0);
    const range = h('input', {
      type: 'range', min: 0, max: String(max), step: '0.5', value: String(c.trim_start || 0),
      'aria-label': 'Where the 59 second cut starts',
      oninput: (e) => {
        const s = Number(e.target.value);
        show(s);
        c.trim_start = s;  // live, so the preview below follows
        const v = document.querySelector(`video[data-clip="${c.id}"]`);
        if (v) { v.currentTime = s; }
      },
      onchange: async (e) => {
        const res = await api('/api/trim', { clip_id: c.id, start: Number(e.target.value) });
        if (!res.ok) return setStatus((res.errors || ['trim failed']).join('\n'), 'err');
        S.clips.find((x) => x.id === c.id).trim_start = res.state.clips.find((x) => x.id === c.id).trim_start;
        renderPanel();
      },
    });
    return h('div', { class: 'trim' }, label, range,
      h('div', { class: 'muted' }, 'Press play to preview exactly this cut. The upload snaps to the nearest earlier keyframe, so it may start up to a second or so sooner.'));
  }

  function noteInput(c) {
    return h('input', {
      type: 'text', class: 'note', maxlength: '200', value: c.note || '',
      placeholder: 'What is this? e.g. Costume contest winners 🏆',
      'aria-label': 'Caption line for this clip',
      onchange: async (e) => {
        const res = await api('/api/note', { clip_id: c.id, note: e.target.value });
        if (!res.ok) return setStatus((res.errors || ['failed']).join('\n'), 'err');
        S.clips.find((x) => x.id === c.id).note = res.state.clips.find((x) => x.id === c.id).note;
        selectionChanged();
      },
    });
  }

  function card(c) {
    const song = songOf(c);
    const pos = selected.indexOf(c.id);
    const isSel = pos >= 0;
    const media = h('div', { class: 'media' });
    if (playing === c.id && c.local) {
      media.append(previewVideo(c));
    } else {
      if (c.has_thumb) media.append(h('img', { src: `/thumbs/${c.id}.jpg`, alt: '', loading: 'lazy' }));
      else media.append(h('div', { class: 'noimg' }, c.error || 'no thumbnail'));
      if (c.local) {
        media.append(h('button', {
          class: 'play', type: 'button', 'aria-label': 'Play clip',
          onclick: () => { playing = c.id; renderGrid(); },
        }, '▶'));
      }
    }
    if (isSel) media.append(h('span', { class: 'order-badge' }, pos + 1));
    if (c.duration) {
      const long = c.duration > S.ig_max_s;
      media.append(h('span', { class: 'dur', title: long ? `Instagram allows up to a minute per video; ${fmtDur(S.ig_max_s)} is posted (choose where it starts below)` : '' },
        long ? `${fmtDur(c.duration)} → ${fmtDur(S.ig_max_s)}` : fmtDur(c.duration)));
    }

    const info = h('div', { class: 'info' },
      h('div', { class: 'time' }, `${hhmmss(c.start)}`,
        c.offset_s !== null && c.offset_s !== undefined && c.song_idx === c.auto_song_idx
          ? ` · ${c.offset_s}s into song` : ''),
      h('div', { class: 'song' }, song ? song.song : (c.custom ? (c.note || 'Other (add a label)') : 'No song matched')),
      song ? h('div', { class: 'performers' }, song.performers.map((p) => h('span', {
        class: `perf ${p.consent}`, title: `${p.consent.toUpperCase()}: ${REASONS[p.reason] || p.reason}`,
      }, { yes: '✓', no: '✗', unknown: '?' }[p.consent] || '?', ' ', p.name,
      p.instagram ? ` @${p.instagram}` : ''))) : null,
      h('div', { class: 'muted' }, c.custom ? 'Custom clip (non-singing)' : (STATUS_LABEL[c.status] || c.status)),
    );

    if (c.flags.length && !c.reviewed) {
      info.append(h('div', { class: 'flag' }, '⚑ Check singer: ', c.flags.map((f) => FLAGS[f] || f).join('; ')));
    }
    if (c.reviewed) info.append(h('div', { class: 'reviewed' }, '✓ singer checked'));
    info.append(reassignSelect(c));
    if (c.custom) info.append(noteInput(c));
    if (isLong(c)) info.append(trimControl(c));

    const canSelect = c.local && (c.status === 'eligible' || c.status === 'unknown');
    const full = !isSel && selected.length >= S.max_clips;
    info.append(h('button', {
      class: `btn sel${isSel ? ' on' : ''}`, type: 'button',
      disabled: !isSel && (!canSelect || full),
      title: !canSelect ? (c.local ? 'Not postable' : 'Not downloaded') : (full ? `Max ${S.max_clips} clips` : ''),
      onclick: () => toggle(c),
    }, isSel ? `✓ Selected #${pos + 1}` : (full && canSelect ? `Full (${S.max_clips})` : 'Select')));

    return h('article', { class: `card st-${c.status}${isSel ? ' selected' : ''}`, id: `clip-${c.id}` }, media, info);
  }

  function reassignSelect(c) {
    const sel = h('select', {
      'aria-label': 'Which song is this clip?',
      onchange: async (e) => {
        const v = e.target.value;
        const res = await api('/api/reassign', { clip_id: c.id, song_idx: v === '' ? null : (v === 'custom' ? -1 : Number(v)) });
        if (!res.ok) return setStatus((res.errors || ['reassign failed']).join('\n'), 'err');
        applyState(res.state, { keepSelection: true });
      },
    });
    sel.append(h('option', { value: '', selected: c.song_idx === null && !c.custom }, '— not a performance / unassigned —'));
    sel.append(h('option', { value: 'custom', selected: !!c.custom }, '✎ Other (not singing): write my own label…'));
    for (const i of c.candidates) {
      const s = S.songs[i];
      const who = s.performers.map((p) => p.name).join(' & ');
      sel.append(h('option', { value: String(i), selected: c.song_idx === i },
        `${hhmm(s.start)} ${who} — ${s.song}${i === c.auto_song_idx ? ' (auto)' : ''}`));
    }
    return sel;
  }

  function renderPanel() {
    const clips = byId();
    $('sel-count').textContent = `${selected.length}/${S.max_clips}`;
    const ol = $('order');
    ol.replaceChildren();
    if (!selected.length) ol.append(h('li', { class: 'empty' }, 'Nothing selected yet.'));
    selected.forEach((id, i) => {
      const c = clips[id];
      const s = songOf(c);
      const li = h('li', { draggable: 'true', dataset: { id } },
        h('span', { class: 'n' }, i + 1),
        c.has_thumb ? h('img', { src: `/thumbs/${c.id}.jpg`, alt: '' }) : h('span'),
        h('span', { class: 'lbl', title: s ? `${s.singer} — ${s.song}` : c.filename },
          `${hhmm(c.start)} ${s ? s.performers.map((p) => p.name).join(' & ') : (c.note || '?')}`,
          isLong(c) ? ` ✂${fmtDur(c.trim_start || 0)}` : '',
          c.status === 'unknown' ? ' (?)' : ''),
        h('span', { class: 'mv' },
          h('button', { type: 'button', 'aria-label': 'Move up', disabled: i === 0, onclick: () => move(i, i - 1) }, '↑'),
          h('button', { type: 'button', 'aria-label': 'Move down', disabled: i === selected.length - 1, onclick: () => move(i, i + 1) }, '↓'),
          h('button', { type: 'button', 'aria-label': 'Remove', onclick: () => toggle(c) }, '×')));
      wireDrag(li);
      ol.append(li);
    });

    const ul = $('uncovered');
    ul.replaceChildren();
    const unc = uncovered();
    if (!S.consenting.length) ul.append(h('li', { class: 'ok' }, 'No consenting singers with clips.'));
    else if (!unc.length) ul.append(h('li', { class: 'ok' }, `✓ All ${S.consenting.length} consenting singers covered`));
    else unc.forEach((n) => ul.append(h('li', {}, n)));

    updateCapCount();
  }

  // Client-side mirror of select.uncovered_performers so it updates instantly.
  function uncovered() {
    const clips = byId();
    const covered = new Set();
    for (const id of selected) {
      const s = songOf(clips[id]);
      if (s) s.performers.forEach((p) => covered.add(p.name.trim().toLowerCase().replace(/\s+/g, ' ')));
    }
    return S.consenting.filter((n) => !covered.has(n.trim().toLowerCase().replace(/\s+/g, ' ')));
  }

  function updateCapCount() {
    const n = $('caption').value.length;
    const el = $('cap-count');
    el.textContent = `${n}/${IG_CAPTION_MAX}`;
    el.style.color = n > IG_CAPTION_MAX ? '#fca5a5' : '';
  }

  // ---------------------------------------------------------------- actions
  async function selectionChanged() {
    renderGrid();
    renderPanel();
    if (!captionDirty) {
      const res = await api('/api/caption', { selected });
      if (res.ok) { $('caption').value = res.caption; updateCapCount(); }
    }
  }

  function toggle(c) {
    const i = selected.indexOf(c.id);
    if (i >= 0) {
      selected.splice(i, 1);
    } else {
      if (selected.length >= S.max_clips) return setStatus(`Instagram allows at most ${S.max_clips} items.`, 'err');
      if (c.status === 'unknown') {
        const s = songOf(c);
        const who = s ? s.performers.filter((p) => p.consent !== 'yes').map((p) => p.name).join(', ') : '?';
        if (!confirm(`Consent is UNKNOWN for: ${who}.\n\nOnly include this if you know they're happy to be posted. Include it?`)) return;
      }
      // Keep show order by default: insert where its time fits.
      const clips = byId();
      let at = selected.findIndex((id) => clips[id].start > c.start);
      if (at < 0) at = selected.length;
      selected.splice(at, 0, c.id);
    }
    setStatus('');
    selectionChanged();
  }

  function move(from, to) {
    if (to < 0 || to >= selected.length) return;
    const [id] = selected.splice(from, 1);
    selected.splice(to, 0, id);
    selectionChanged();
  }

  let dragId = null;
  function wireDrag(li) {
    li.addEventListener('dragstart', (e) => {
      dragId = li.dataset.id; li.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', dragId);
    });
    li.addEventListener('dragend', () => { li.classList.remove('dragging'); dragId = null; });
    li.addEventListener('dragover', (e) => { e.preventDefault(); li.classList.add('over'); });
    li.addEventListener('dragleave', () => li.classList.remove('over'));
    li.addEventListener('drop', (e) => {
      e.preventDefault(); li.classList.remove('over');
      const from = selected.indexOf(dragId);
      const to = selected.indexOf(li.dataset.id);
      if (from >= 0 && to >= 0 && from !== to) move(from, to);
    });
  }

  async function saveDraft(quiet) {
    const res = await api('/api/draft', { selected, caption: $('caption').value });
    if (!res.ok) { setStatus((res.errors || ['save failed']).join('\n'), 'err'); return false; }
    if (!quiet) setStatus(`Draft saved → ${res.path}`, 'ok');
    return true;
  }

  async function publish() {
    if (!selected.length) return setStatus('Select some clips first.', 'err');
    if ($('caption').value.length > IG_CAPTION_MAX) return setStatus('Caption is longer than Instagram allows.', 'err');
    const clips = byId();
    const unknown = selected.filter((id) => clips[id].status === 'unknown');
    const uncov = uncovered();
    let msg = `Open Instagram${$('facebook').checked ? ' and Facebook' : ''} and stage a ${selected.length}-item post?` +
      (S.auto_share ? '\n\n--auto-share is ON: it WILL click Share.' : '\n\nIt stops before Share so you can check it.');
    if (unknown.length) msg += `\n\n${unknown.length} clip(s) have UNKNOWN consent.`;
    if (uncov.length) msg += `\n\nNot covered: ${uncov.join(', ')}`;
    if (!confirm(msg)) return;
    let force = false;
    if (S.posted) {
      if (!confirm(`This night was already marked as posted (${S.posted.posted_at}). Post AGAIN?`)) return;
      force = true;
    }
    setStatus($('facebook').checked ? 'Starting Instagram + Facebook publishers…' : 'Starting publisher…');
    const res = await api('/api/publish', {
      selected, caption: $('caption').value, allow_unknown: unknown.length > 0, force, facebook: $('facebook').checked,
    });
    if (!res.ok) return setStatus((res.errors || ['publish failed']).join('\n'), 'err');
    if (res.facebook && res.facebook.error) setStatus(`Instagram started. ${res.facebook.error}`, 'err');
    else setStatus(res.message || 'Publisher started.', 'ok');
  }

  async function markPosted() {
    if (!confirm('Mark this night as posted? (Do this after Instagram shows it shared.)')) return;
    if (!(await saveDraft(true))) return;
    const res = await api('/api/posted', {});
    if (!res.ok) return setStatus((res.errors || ['failed']).join('\n'), 'err');
    S.posted = res.posted;
    renderBanners();
    setStatus('Marked as posted. 🎉', 'ok');
  }

  function applyState(state, { keepSelection } = {}) {
    S = state;
    if (keepSelection) {
      const ok = new Set(S.selected);
      selected = selected.filter((id) => ok.has(id) || S.clips.some((c) => c.id === id && (c.status === 'eligible' || c.status === 'unknown')));
    } else {
      selected = [...S.selected];
      $('caption').value = S.caption || '';
      captionDirty = !!S.draft_loaded;
    }
    render();
  }

  // ---------------------------------------------------------------- boot
  $('show-excluded').addEventListener('change', renderGrid);
  $('selected-only').addEventListener('change', renderGrid);
  $('caption').addEventListener('input', () => { captionDirty = true; updateCapCount(); });
  $('regen').addEventListener('click', async () => {
    captionDirty = false;
    const res = await api('/api/caption', { selected });
    if (res.ok) { $('caption').value = res.caption; updateCapCount(); }
  });
  $('save').addEventListener('click', () => saveDraft(false));
  $('publish').addEventListener('click', publish);
  $('posted').addEventListener('click', markPosted);

  api('/api/state').then((st) => applyState(st)).catch((e) => setStatus(`Failed to load: ${e}`, 'err'));
})();
