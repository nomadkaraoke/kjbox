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
  // Who is in the clip: hand-edited names win over the logged song's performers.
  const perfsOf = (c) => { const s = c && songOf(c); return s ? (c.performers || s.performers) : []; };

  async function editPerformers(c) {
    const cur = perfsOf(c).map((p) => p.name).join(' & ');
    const logged = songOf(c).performers.map((p) => p.name).join(' & ');
    const v = prompt(`Who is singing in this clip? Separate people with &.\nLogged as: ${logged}\nLeave empty to go back to that.`, cur);
    if (v === null) return;
    const res = await api('/api/performers', { clip_id: c.id, names: v });
    if (!res.ok) return setStatus((res.errors || ['edit failed']).join('\n'), 'err');
    const was = selected.includes(c.id);
    applyState(res.state, { keepSelection: true });
    await selectionChanged();  // caption + per-singer counts follow the new names
    const now = S.clips.find((x) => x.id === c.id);
    if (was && !selected.includes(c.id)) setStatus(`Removed from the carousel: someone in it said no.`, 'err');
    else if (now && now.status === 'unknown') setStatus(`Consent unknown for someone in this clip; you'll be asked to confirm before publishing.`);
  }

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
  const cutStart = (c) => c.trim_start || 0;
  // Where the posted cut ends: the chosen end, else the clip's end, never more than 59s after the start.
  const cutEnd = (c) => Math.min(c.duration || Infinity,
    c.trim_end === null || c.trim_end === undefined ? Infinity : c.trim_end, cutStart(c) + S.ig_max_s);
  const isCut = (c) => c.duration && (cutStart(c) > 0 || cutEnd(c) < c.duration - 0.05);
  const fmtT = (s) => { const m = Math.floor(s / 60); const r = s - m * 60; return `${m}:${r.toFixed(1).padStart(4, '0')}`; };

  async function saveTrim(c, start, end) {
    const res = await api('/api/trim', { clip_id: c.id, start, end });
    if (!res.ok) { setStatus((res.errors || ['trim failed']).join('\n'), 'err'); return false; }
    const now = res.state.clips.find((x) => x.id === c.id);
    const mine = S.clips.find((x) => x.id === c.id);
    mine.trim_start = now.trim_start;
    mine.trim_end = now.trim_end;
    renderGrid();
    renderPanel();
    return true;
  }

  // The player plays only the part that gets posted (start → end), with its own seek bar and
  // ±1s/±5s buttons: Chrome drops the native timeline on a player this narrow.
  function previewVideo(c) {
    const v = h('video', { src: `/media/${c.id}`, autoplay: true, playsinline: true, preload: 'auto' });
    v.dataset.clip = c.id;
    const dur = () => (Number.isFinite(v.duration) ? v.duration : (c.duration || 0));
    const seek = h('input', { type: 'range', min: '0', max: String(c.duration || 0), step: '0.1', value: '0',
      'aria-label': 'Seek within the clip' });
    const time = h('span', { class: 'ptime' });
    const playBtn = h('button', { type: 'button', class: 'pbtn', 'aria-label': 'Play or pause' }, '⏸');
    const window_ = h('div', { class: 'pwin' });
    const draw = () => {
      const d = dur() || 1;
      seek.max = String(d);
      seek.value = String(v.currentTime);
      time.textContent = `${fmtT(v.currentTime)} / ${fmtT(d)}`;
      playBtn.textContent = v.paused ? '▶' : '⏸';
      window_.style.left = `${(cutStart(c) / d) * 100}%`;
      window_.style.width = `${((cutEnd(c) - cutStart(c)) / d) * 100}%`;
    };
    // Playing past the cut's end loops back to its start, so you only ever hear what's posted.
    // Seeking by hand outside the cut is allowed (to find a new start or end) and just pauses at the end.
    let manual = false;
    v.addEventListener('loadedmetadata', () => { v.currentTime = cutStart(c); draw(); });
    v.addEventListener('timeupdate', () => {
      if (!manual && v.currentTime >= cutEnd(c)) { v.pause(); v.currentTime = cutStart(c); }
      draw();
    });
    v.addEventListener('play', () => {
      if (v.currentTime < cutStart(c) - 0.05 || v.currentTime >= cutEnd(c) - 0.05) { v.currentTime = cutStart(c); manual = false; }
      else manual = false;
      draw();
    });
    v.addEventListener('pause', draw);
    v.addEventListener('click', () => (v.paused ? v.play() : v.pause()));
    const jump = (t) => { manual = true; v.currentTime = Math.max(0, Math.min(dur(), t)); draw(); };
    seek.addEventListener('input', () => jump(Number(seek.value)));
    playBtn.addEventListener('click', () => (v.paused ? v.play() : v.pause()));
    const step = (d, label) => h('button', { type: 'button', class: 'pbtn', 'aria-label': `${label}`, title: label,
      onclick: () => jump(v.currentTime + d) }, d > 0 ? `+${d}` : `${d}`);
    const here = (which) => h('button', {
      type: 'button', class: 'pbtn wide',
      title: which === 'start' ? 'Posted cut starts here (cuts off everything before)' : 'Posted cut ends here (cuts off everything after)',
      onclick: async () => {
        const t = Math.round(v.currentTime * 10) / 10;
        v.pause();
        if (which === 'start') {
          const end = c.trim_end !== null && c.trim_end !== undefined && c.trim_end > t ? c.trim_end : null;
          await saveTrim(c, t, end);
        } else {
          await saveTrim(c, Math.min(cutStart(c), Math.max(0, t - 1)), t);
        }
      },
    }, which === 'start' ? '⟦ Start here' : 'End here ⟧');
    const bar = h('div', { class: 'player' },
      h('div', { class: 'pseek' }, window_, seek),
      h('div', { class: 'prow' }, playBtn, step(-5, 'Back 5 seconds'), step(-1, 'Back 1 second'),
        step(1, 'Forward 1 second'), step(5, 'Forward 5 seconds'), time),
      h('div', { class: 'prow' }, here('start'), here('end')));
    return { video: v, bar };
  }

  // Start and length of the posted cut, for every clip. Typing a number or using
  // "Start here" / "End here" in the player both land here.
  function trimControl(c) {
    const start = cutStart(c);
    const len = Math.round((cutEnd(c) - start) * 10) / 10;
    const maxLen = Math.min(S.ig_max_s, c.duration - start);
    const startIn = h('input', { type: 'number', min: '0', max: String(Math.max(0, c.duration - 1)), step: '0.5',
      value: String(start), 'aria-label': 'Seconds to cut off the start' });
    const lenIn = h('input', { type: 'number', min: '1', max: String(Math.round(maxLen * 10) / 10), step: '0.5',
      value: String(len), 'aria-label': 'Length in seconds' });
    const apply = async () => {
      const s = Math.max(0, Number(startIn.value) || 0);
      const l = Number(lenIn.value);
      const end = Number.isFinite(l) && l > 0 ? s + l : null;
      // A length that just reaches the clip's end (or the 59s limit) means "no limit": keep it
      // following the start when you move it.
      const natural = Math.min(c.duration, s + S.ig_max_s);
      await saveTrim(c, s, end === null || end >= natural - 0.05 ? null : end);
    };
    startIn.addEventListener('change', apply);
    lenIn.addEventListener('change', apply);
    const kids = [
      h('div', { class: 'trim-row' },
        h('label', {}, 'Cut first ', startIn, ' s'),
        h('label', {}, 'Length ', lenIn, ' s')),
      h('div', { class: 'trim-lbl' + (isCut(c) ? ' on' : '') },
        isCut(c) ? `✂ Posts ${fmtT(start)} → ${fmtT(cutEnd(c))} (${len}s of ${fmtDur(c.duration)})`
          : `Posts the whole clip (${fmtDur(c.duration)})`),
    ];
    if (isCut(c)) {
      kids.push(h('button', { type: 'button', class: 'linkish', onclick: () => saveTrim(c, 0, null) },
        isLong(c) ? 'Reset (first 59s)' : 'Reset (whole clip)'));
    }
    if (isLong(c) && !isCut(c)) {
      kids[1].textContent = `✂ Posts the first ${fmtDur(S.ig_max_s)} of ${fmtDur(c.duration)} (Instagram's limit)`;
    }
    return h('div', { class: 'trim' }, ...kids);
  }

  // Instagram handle for a singer, saved to [handles] in config.toml for every future night too.
  async function editHandle(name, current) {
    const v = prompt(`Instagram handle for ${name}?\nSaved to your config for future nights too. Leave empty to forget it.`, current ? `@${current}` : '');
    if (v === null) return;
    const res = await api('/api/handle', { name, handle: v });
    if (!res.ok) return setStatus((res.errors || ['saving the handle failed']).join('\n'), 'err');
    applyState(res.state, { keepSelection: true });
    await selectionChanged();
    if (!res.handle) setStatus(`Forgot ${name}'s handle.`, 'ok');
    else if (res.tagged) setStatus(`Saved @${res.handle} for ${name}.${captionDirty ? ' Click Regenerate to put it in the caption.' : ''}`, 'ok');
    else setStatus(`Saved @${res.handle} for ${name}, but they're not tagged tonight: they didn't say yes to photos.`);
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
    let player = null;
    if (playing === c.id && c.local) {
      player = previewVideo(c);
      media.append(player.video);
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
      const cut = isCut(c) || isLong(c);
      media.append(h('span', { class: 'dur', title: cut ? `${fmtDur(cutEnd(c) - cutStart(c))} of this clip is posted (set the cut below)` : '' },
        cut ? `${fmtDur(c.duration)} → ${fmtDur(cutEnd(c) - cutStart(c))}` : fmtDur(c.duration)));
    }

    const info = h('div', { class: 'info' },
      h('div', { class: 'time' }, `${hhmmss(c.start)}`,
        c.offset_s !== null && c.offset_s !== undefined && c.song_idx === c.auto_song_idx
          ? ` · ${c.offset_s}s into song` : ''),
      h('div', { class: 'song' }, song ? song.song : (c.custom ? (c.note || 'Other (add a label)') : 'No song matched')),
      song ? h('div', { class: 'performers' }, perfsOf(c).map((p) => h('span', {
        class: `perf ${p.consent}`, title: `${String(p.consent).toUpperCase()}: ${REASONS[p.reason] || p.reason}`,
      }, { yes: '✓', no: '✗', unknown: '?' }[p.consent] || '?', ' ', p.name,
      p.instagram ? ` @${p.instagram}` : '',
      p.consent === 'yes' ? h('button', { class: 'edit-handle', type: 'button', title: `Set ${p.name}'s Instagram handle`,
        'aria-label': `Set ${p.name}'s Instagram handle`, onclick: () => editHandle(p.name, p.instagram) }, p.instagram ? '✎' : '+@') : null)),
      h('button', { class: 'edit-perf', type: 'button', title: 'Edit who is singing in this clip', 'aria-label': 'Edit singers',
        onclick: () => editPerformers(c) }, '✎'),
      c.performers ? h('span', { class: 'muted' }, ` (edited; logged as ${song.performers.map((p) => p.name).join(' & ')})`) : '') : null,
      h('div', { class: 'muted' }, c.custom ? 'Custom clip (non-singing)' : (STATUS_LABEL[c.status] || c.status)),
    );

    if (c.flags.length && !c.reviewed) {
      info.append(h('div', { class: 'flag' }, '⚑ Check singer: ', c.flags.map((f) => FLAGS[f] || f).join('; ')));
    }
    if (c.reviewed) info.append(h('div', { class: 'reviewed' }, '✓ singer checked'));
    info.append(reassignSelect(c));
    if (c.custom) info.append(noteInput(c));
    if (c.local && c.duration) info.append(trimControl(c));

    const canSelect = c.local && (c.status === 'eligible' || c.status === 'unknown');
    const full = !isSel && selected.length >= S.max_clips;
    info.append(h('button', {
      class: `btn sel${isSel ? ' on' : ''}`, type: 'button',
      disabled: !isSel && (!canSelect || full),
      title: !canSelect ? (c.local ? 'Not postable' : 'Not downloaded') : (full ? `Max ${S.max_clips} clips` : ''),
      onclick: () => toggle(c),
    }, isSel ? `✓ Selected #${pos + 1}` : (full && canSelect ? `Full (${S.max_clips})` : 'Select')));

    return h('article', { class: `card st-${c.status}${isSel ? ' selected' : ''}`, id: `clip-${c.id}` }, media, player ? player.bar : null, info);
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
          `${hhmm(c.start)} ${s ? perfsOf(c).map((p) => p.name).join(' & ') : (c.note || '?')}`,
          isCut(c) || isLong(c) ? ` ✂${fmtDur(cutStart(c))}–${fmtDur(cutEnd(c))}` : '',
          c.status === 'unknown' ? ' (?)' : ''),
        h('span', { class: 'mv' },
          s ? h('button', { type: 'button', 'aria-label': 'Edit singers', title: 'Edit who is singing', onclick: () => editPerformers(c) }, '✎') : '',
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

    const ps = $('per-singer');
    ps.replaceChildren();
    const counts = clipsPerSinger();
    if (!counts.length) ps.append(h('li', { class: 'muted' }, 'No clips selected.'));
    else counts.forEach((r) => ps.append(h('li', {},
      h('span', { class: 'cnt' }, `${r.n}×`), ' ', r.name,
      r.handle ? h('span', { class: 'muted' }, ` @${r.handle}`) : '',
      r.person ? h('button', { class: 'edit-handle', type: 'button', title: `Set ${r.name}'s Instagram handle`,
        'aria-label': `Set ${r.name}'s Instagram handle`, onclick: () => editHandle(r.name, r.handle) },
      r.handle ? '✎' : '+ @handle') : null)));

    updateCapCount();
  }

  // How many selected clips each performer appears in (a duet counts for both).
  // "Other" clips count under their label. Most clips first, then show order.
  function clipsPerSinger() {
    const clips = byId();
    const rows = new Map();
    const bump = (key, name, handle, person) => {
      const r = rows.get(key) || { name, handle: '', n: 0, order: rows.size, person };
      r.n += 1;
      if (handle && !r.handle) r.handle = handle;
      rows.set(key, r);
    };
    for (const id of selected) {
      const c = clips[id];
      const s = songOf(c);
      if (s) {
        const seen = new Set();
        perfsOf(c).forEach((p) => {
          const key = p.name.trim().toLowerCase().replace(/\s+/g, ' ');
          if (seen.has(key)) return;
          seen.add(key);
          bump(key, p.name, (p.instagram || '').replace(/^@/, ''), p.consent === 'yes');
        });
      } else if (c && c.note) {
        bump(`other:${c.note}`, `✎ ${c.note}`, '', false);
      }
    }
    return [...rows.values()].sort((a, b) => b.n - a.n || a.order - b.order);
  }

  // Client-side mirror of select.uncovered_performers so it updates instantly.
  function uncovered() {
    const clips = byId();
    const covered = new Set();
    for (const id of selected) {
      perfsOf(clips[id]).forEach((p) => covered.add(p.name.trim().toLowerCase().replace(/\s+/g, ' ')));
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
        const who = perfsOf(c).filter((p) => p.consent !== 'yes').map((p) => p.name).join(', ') || '?';
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
