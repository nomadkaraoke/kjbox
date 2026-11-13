/* "How Auto Order works" — KJ explainer modal.
 *
 * Everything shown here comes from GET /rotation/auto-order/explain, which runs
 * curated example rotations (auto_order_explain.py) and a dry run of tonight's
 * queue through the REAL Auto Order algorithm with per-slot score tracing. So the
 * before/after pictures and the "why did X win this spot" breakdowns are exactly
 * what the code does — not a drawing of it. Read-only: nothing here changes the
 * rotation.
 */
(function () {
    'use strict';

    let aoData = null;
    let aoRequestSeq = 0;
    let aoTab = 'how';
    // Which after-row's decision is open, per panel key ("scn:<id>" / "live").
    const aoOpenSlot = {};

    const esc = (v) => String(v == null ? '' : v)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

    const TERM_LABELS = {
        priority: 'KJ bump',
        bump_top5: 'already in the top 5',
        overdue: 'overdue (waited too long)',
        new_singer: 'new singer',
        fairness: 'fewer songs tonight',
        wait: 'waiting time',
        spacing: 'sang too recently',
        stick: 'stay near current spot',
    };
    const TERM_ORDER = ['priority', 'bump_top5', 'overdue', 'new_singer', 'fairness', 'wait', 'spacing', 'stick'];

    // Per-panel singer colours: assigned in order of first appearance and spaced by
    // the golden angle, so neighbouring singers never share a colour. Set by
    // renderBeforeAfter / aoToggleSlot before rendering a panel's rows.
    let hueMap = {};
    const firstMember = (name) => String(name || '').split('&')[0].trim().toLowerCase();
    function setHues(rows) {
        hueMap = {};
        let n = 0;
        rows.forEach((r) => {
            const k = firstMember(r.singer);
            if (!(k in hueMap)) hueMap[k] = Math.round((n++ * 137.508 + 300) % 360);
        });
    }
    function singerHue(name) {
        const k = firstMember(name);
        return k in hueMap ? hueMap[k] : 0;
    }

    function fmtNum(n) {
        const r = Math.round(n);
        return (r > 0 ? '+' : r < 0 ? '−' : '') + Math.abs(r).toLocaleString();
    }

    function fmtWait(m) {
        if (m == null) return 'no wait yet';
        if (m >= 60) return `waited ${Math.floor(m / 60)}h ${String(Math.round(m % 60)).padStart(2, '0')}m`;
        return `waited ${Math.round(m)}m`;
    }

    // ---------------------------------------------------------------
    // Open / close / tabs / print
    // ---------------------------------------------------------------
    window.openAutoOrderExplainer = async function (tab) {
        document.getElementById('ao-explain-modal').classList.remove('hidden');
        aoShowTab(tab || aoTab);
        const body = document.getElementById('ao-explain-body');
        body.innerHTML = '<div class="ao-muted">Loading&hellip;</div>';
        // Only the latest request may paint: a slower, older response (e.g. from
        // before a close + reopen) must not overwrite a newer queue preview.
        const req = ++aoRequestSeq;
        try {
            const resp = await fetch('/rotation/auto-order/explain');
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            if (req !== aoRequestSeq) return;
            aoData = data;
            render();
        } catch (e) {
            if (req !== aoRequestSeq) return;
            body.innerHTML = `<div class="ao-error">Couldn't load the explainer (${esc(e.message)}).</div>`;
        }
    };

    window.closeAutoOrderExplainer = function () {
        aoRequestSeq++;   // drop any in-flight response
        document.getElementById('ao-explain-modal').classList.add('hidden');
    };

    window.aoShowTab = function (tab) {
        aoTab = tab;
        document.querySelectorAll('#ao-explain-modal .ao-tab').forEach((b) => {
            const on = b.dataset.tab === tab;
            b.classList.toggle('active', on);
            b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        document.querySelectorAll('#ao-explain-modal .ao-panel').forEach((p) => {
            p.classList.toggle('hidden', p.dataset.panel !== tab);
        });
    };

    window.printAutoOrderExplainer = function () {
        // Print the guide + all examples (not tonight's live queue — it has real names
        // and is a moment-in-time preview, not documentation).
        document.body.classList.add('ao-printing');
        const cleanup = () => {
            document.body.classList.remove('ao-printing');
            window.removeEventListener('afterprint', cleanup);
        };
        window.addEventListener('afterprint', cleanup);
        window.print();
        setTimeout(cleanup, 1000);
    };

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const m = document.getElementById('ao-explain-modal');
            if (m && !m.classList.contains('hidden')) closeAutoOrderExplainer();
        }
    });

    // ---------------------------------------------------------------
    // Rendering
    // ---------------------------------------------------------------
    function render() {
        const body = document.getElementById('ao-explain-body');
        body.innerHTML =
            `<section class="ao-panel" data-panel="how">${renderHow(aoData.config, aoData.scenarios)}</section>` +
            `<section class="ao-panel" data-panel="examples">${renderExamples(aoData.scenarios)}</section>` +
            `<section class="ao-panel ao-no-print" data-panel="live">${renderLive(aoData.live)}</section>`;
        aoShowTab(aoTab);
    }

    function renderHow(c, scenarios) {
        const w = c.weights;
        const overdueMax = Math.round(w.overdue * c.overdue_max_steps);
        const waitMax = Math.round(w.wait * w.wait_overflow_cap);
        const failing = scenarios.filter((s) => !s.all_ok).length;
        const ladder = [
            ['Never twice in a row', 'Effectively forbidden. Only happens if literally nobody else is left.', 'veto', 'ao-lv-veto'],
            ['KJ bump up / down', 'Your bump (or a confirmed tip at or over the threshold) beats everything below.', fmtNum(w.priority), 'ao-lv-kj'],
            ['Already in the top 5', 'A second copy of someone in rows 1-3, sitting at row 4 or 5, is pushed out of the top five.', fmtNum(-w.bump_top5), 'ao-lv-strong'],
            ['Overdue', `Builds once someone would wait more than ${c.max_wait_minutes} min: ${fmtNum(w.overdue)} for every ${c.overdue_step_minutes} min over, up to ${fmtNum(overdueMax)}.`, `up to ${fmtNum(overdueMax)}`, 'ao-lv-strong'],
            ['New singer', 'One-off boost for someone who hasn’t sung yet tonight (first song only).', fmtNum(w.new_singer), 'ao-lv-mid'],
            ['Sang too recently', `Penalty for a repeat within ${c.spread_target} slots of that person's other song; the closer the repeat, the bigger the penalty.`, `up to ${fmtNum(-w.spacing)}`, 'ao-lv-mid'],
            ['Fewer songs tonight', `${Math.round(w.fairness)} ÷ (1 + songs sung): 0 songs ${fmtNum(w.fairness)}, 1 song ${fmtNum(w.fairness / 2)}, 3 songs ${fmtNum(w.fairness / 4)}, 6 songs ${fmtNum(w.fairness / 7)}.`, `up to ${fmtNum(w.fairness)}`, 'ao-lv-mild'],
            ['Waiting time', `Gentle pull for a longer wait; reaches ${fmtNum(w.wait)} at ${c.max_wait_minutes} min, capped at ${fmtNum(waitMax)}.`, `up to ${fmtNum(waitMax)}`, 'ao-lv-mild'],
            ['Stay where you are', `Small cost for moving an entry (about \u2212${(w.stick / 10).toFixed(1)} per row moved), so a fair queue isn’t shuffled for nothing. New singers don’t pay it.`, 'small', 'ao-lv-mild'],
        ];
        return `
        <p class="ao-lead">Auto Order reorders the rotation the way an experienced KJ would: <strong>fair to
        everyone, nobody twice in a row, nobody waiting forever</strong>. Those goals pull against each other,
        so it's a best-effort balance. The aim is what a good KJ would choose, not a mathematically perfect
        answer.</p>
        ${failing ? `<div class="ao-error">${failing} example(s) no longer match their description. The algorithm has changed since this guide was written.</div>` : ''}

        <h4 class="ao-h">Hard rules: never broken</h4>
        <div class="ao-rules">
            <div class="ao-rule"><span class="ao-rule-ico">&#x1F512;</span><div><strong>Rows 1&ndash;${c.always_lock} never move.</strong> Those singers are about to go on and already know it.</div></div>
            <div class="ao-rule"><span class="ao-rule-ico">&#x1F512;</span><div><strong>Rows ${c.always_lock + 1}&ndash;${c.lock_head} stay put too</strong>, with one exception: a second song by someone already in rows 1&ndash;${c.always_lock} is moved out of the top five.</div></div>
            <div class="ao-rule"><span class="ao-rule-ico">&#x2195;&#xFE0F;</span><div><strong>Each singer's own songs keep their order.</strong> Someone's next song is always the one they asked for next (singers can ask to swap their own songs around from their phone).</div></div>
            <div class="ao-rule"><span class="ao-rule-ico">&#x23EC;</span><div><strong>&ldquo;Being Made&rdquo; tracks stay at the bottom</strong> (even from rows 1&ndash;3) until you switch them back to Waiting, because they can't be played yet.</div></div>
            <div class="ao-rule"><span class="ao-rule-ico">&#x26D4;</span><div><strong>Never the same person twice in a row</strong> (duets count for each person in them), unless nobody else is left.</div></div>
        </div>

        <h4 class="ao-h">How each spot is filled</h4>
        <div class="ao-steps">
            <div class="ao-step"><span class="ao-step-n">1</span><div>Start at the first spot that's allowed to change (normally row ${c.lock_head + 1}) and work down one row at a time.</div></div>
            <div class="ao-step"><span class="ao-step-n">2</span><div>The contenders for that spot are <em>each singer's next song</em>, one per person.</div></div>
            <div class="ao-step"><span class="ao-step-n">3</span><div>Score every contender with the list below. The highest score gets the spot; the rest try again for the next one.</div></div>
        </div>
        <p class="ao-note ao-hint">In the Examples tab, tap any row on the <strong>After</strong> side to see the exact scores that put it there.</p>

        <h4 class="ao-h">What it weighs: strongest first</h4>
        <div class="ao-ladder">
            ${ladder.map(([t, d, v, cls]) => `
            <div class="ao-ladder-row ${cls}">
                <div class="ao-ladder-val">${esc(v)}</div>
                <div><strong>${esc(t)}</strong><div class="ao-ladder-desc">${esc(d)}</div></div>
            </div>`).join('')}
        </div>

        <h4 class="ao-h">Two ideas that make it feel fair</h4>
        <div class="ao-ideas">
            <div class="ao-idea"><strong>It looks ahead.</strong> Everything is scored as of the moment that spot will
            actually come up. Someone's 2nd queued song is treated as if they've already sung their 1st, and their wait
            includes all the songs still ahead of them, using each track's real length plus
            ${c.turnaround_minutes * 60} seconds for the changeover (${c.avg_song_minutes} min if a track isn't linked).</div>
            <div class="ao-idea"><strong>The wait clock resets when you're scheduled.</strong> If someone has waited
            90 minutes, their first song gets pulled up. Their next song is then judged on the gap since that
            first one, not on the 90 minutes, so a long wait earns one early song, not a run of them.</div>
        </div>

        <h4 class="ao-h">Your controls</h4>
        <ul class="ao-controls">
            <li><strong>Reorder</strong> (Rotation header) runs Auto Order once. It's one Undo step, and if nothing needs to move, nothing happens.</li>
            <li><strong>Auto Order the rotation</strong> (Requests settings) re-runs it automatically whenever a new entry is added. Off by default.</li>
            <li><strong>Bump up / down</strong> (an entry's &hellip; menu, or a singer's Priority menu) sticks every time Auto Order runs. A confirmed tip at or over the threshold applies the same bump up.</li>
            <li><strong>Being Made</strong> status keeps a track at the bottom. Set it back to Waiting once the video is ready.</li>
            <li><strong>Undo</strong> reverses any reorder, including the automatic ones.</li>
        </ul>

        <h4 class="ao-h">Where these rules came from</h4>
        <p class="ao-note">The rules were tuned against about 20 real nights of rotation history, comparing the order
        people submitted in with the order the KJ actually ran by hand. On those nights the KJ kept a median of
        <strong>10 other singers</strong> between someone's repeat songs, and same-person-twice-in-a-row
        happened only about 5% of the time, early in the night when it couldn't be avoided. Before launch, Andrew
        reviewed every decision across 14 before-and-after scenarios in three rounds, and the rules were adjusted
        until he agreed with all 14. Full rationale:
        <code>docs/archive/2026-07-16-auto-order-rotation-plan.md</code>.</p>`;
    }

    function renderExamples(scenarios) {
        const okCount = scenarios.filter((s) => s.all_ok).length;
        return `
        <p class="ao-lead">Each example is a small rotation run through the real Auto Order code every time you open
        this window. Coloured bars are singers, <span class="ao-pill">sung N</span> is songs sung tonight, and
        &#x1F512; marks rows that are locked.<span class="ao-hint"> <strong>Tap a row on the After side to see why it won that spot.</strong></span></p>
        <p class="ao-note">${okCount} of ${scenarios.length} examples checked against the live algorithm${okCount === scenarios.length ? ' ✓' : ''}.</p>
        <nav class="ao-toc">${scenarios.map((s, i) =>
            `<a href="#ao-scn-${esc(s.id)}" onclick="document.getElementById('ao-scn-${esc(s.id)}').scrollIntoView({behavior:'smooth'});return false;">${i + 1}. ${esc(s.title)}</a>`).join('')}</nav>
        ${scenarios.map((s, i) => renderScenario(s, i)).join('')}`;
    }

    function renderScenario(s, i) {
        const key = 'scn:' + s.id;
        return `
        <article class="ao-scn" id="ao-scn-${esc(s.id)}">
            <header class="ao-scn-head">
                <h4>${i + 1}. ${esc(s.title)}</h4>
                <span class="ao-chip">${esc(s.rule)}</span>
            </header>
            <p class="ao-scn-situation">${esc(s.situation)}</p>
            ${renderBeforeAfter(s, key)}
            <div class="ao-scn-text">
                <p><strong>What happens:</strong> ${esc(s.outcome)}</p>
                <p><strong>Why:</strong> ${esc(s.why)}</p>
            </div>
            <ul class="ao-checks">${s.checks.map((c) =>
                `<li class="${c.ok ? 'ok' : 'bad'}">${c.ok ? '✓' : '✗ out of date:'} ${esc(c.text)}</li>`).join('')}</ul>
        </article>`;
    }

    function renderLive(live) {
        if (!live) {
            return '<p class="ao-muted">The rotation isn’t available on this device.</p>';
        }
        if (!live.before.length) {
            return '<p class="ao-muted">The rotation is empty. Once people are queued you’ll see what Auto Order would do with them here.</p>';
        }
        const moved = live.after.filter((r) => r.moved).length;
        return `
        <p class="ao-lead"><strong>Preview only: nothing has been changed.</strong> This is what Auto Order would do
        to tonight's queue if you pressed Reorder now. Tap any row on the After side to see why.</p>
        <p class="ao-note">${moved ? `It would move ${moved} of ${live.after.length} entries.` :
            'Nothing would move: the queue is already in the order Auto Order would choose.'}
        <button class="system-btn ao-refresh" onclick="openAutoOrderExplainer('live')">Refresh</button></p>
        ${renderBeforeAfter(live, 'live')}`;
    }

    function rowHtml(r, side, key, locked) {
        const hue = singerHue(r.singer);
        const badges = [];
        if (locked) badges.push('<span class="ao-b ao-b-lock" title="Locked: will not move">&#x1F512;</span>');
        if (r.being_made) badges.push('<span class="ao-b ao-b-made">being made</span>');
        if (r.priority_bias > 0) badges.push('<span class="ao-b ao-b-bump">⬆ bumped</span>');
        if (r.priority_bias < 0) badges.push('<span class="ao-b ao-b-bump">⬇ bumped down</span>');
        if (!r.sung && !r.being_made) badges.push('<span class="ao-b ao-b-new">new</span>');
        let move = '';
        if (side === 'after' && r.delta) {
            move = r.delta > 0 ? `<span class="ao-mv ao-up" title="Moved up ${r.delta}">↑${r.delta}</span>`
                : `<span class="ao-mv ao-down" title="Moved down ${-r.delta}">↓${-r.delta}</span>`;
        }
        const pos = side === 'after' ? r.row : r.orig_row;
        const clickable = side === 'after';
        const open = clickable && aoOpenSlot[key] === r.row;
        return `<div class="ao-row${clickable ? ' ao-click' : ''}${open ? ' ao-open' : ''}" style="--h:${hue}"
            ${clickable ? `role="button" tabindex="0" onclick="aoToggleSlot('${esc(key)}', ${r.row})"
            onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();aoToggleSlot('${esc(key)}', ${r.row});}"` : ''}>
            <span class="ao-pos">${pos}</span>
            <span class="ao-who"><span class="ao-name">${esc(r.singer)}</span><span class="ao-song">${esc(r.song)}</span></span>
            <span class="ao-meta">${badges.join('')}<span class="ao-pill">sung ${r.sung}</span><span class="ao-pill ao-pill-wait">${esc(fmtWait(r.wait_minutes))}</span></span>
            ${move}
        </div>`;
    }

    function renderBeforeAfter(p, key) {
        setHues(p.before);
        const lockedRows = new Set(p.decisions.filter((d) => d.kind === 'locked').map((d) => d.slot + 1));
        const before = p.before.map((r) => rowHtml(r, 'before', key, false)).join('');
        const after = p.after.map((r) => rowHtml(r, 'after', key, lockedRows.has(r.row))).join('');
        const openRow = aoOpenSlot[key];
        return `
        <div class="ao-ba" data-key="${esc(key)}">
            <div class="ao-col"><div class="ao-col-h">Before</div>${before}</div>
            <div class="ao-col"><div class="ao-col-h">After Auto Order</div>${after}</div>
        </div>
        <div class="ao-decision" data-key="${esc(key)}">${openRow ? renderDecision(p, openRow) : ''}</div>`;
    }

    function renderDecision(p, row) {
        const d = p.decisions[row - 1];
        if (!d) return '';
        const byId = {};
        p.before.forEach((r) => { byId[r.id] = r; });
        const who = byId[d.entry_id] || {};
        const label = `${esc(who.singer)} — ${esc(who.song)}`;
        if (d.kind === 'locked') {
            return `<div class="ao-dec"><div class="ao-dec-h">Row ${row}: ${label}</div>
                <p>&#x1F512; <strong>Locked.</strong> Rows 1&ndash;${aoData.config.always_lock} never move, and rows
                ${aoData.config.always_lock + 1}&ndash;${aoData.config.lock_head} stay put unless they're a second song
                by someone already in rows 1&ndash;${aoData.config.always_lock}. Auto Order didn't choose this spot; it
                planned everything else around it.</p></div>`;
        }
        if (d.kind === 'being_made') {
            return `<div class="ao-dec"><div class="ao-dec-h">Row ${row}: ${label}</div>
                <p>&#x23EC; <strong>Being Made.</strong> This track isn't ready to play, so it stays at the bottom
                (in the order it was added) until you set it back to Waiting.</p></div>`;
        }
        const cands = d.candidates;
        const top = cands.slice(0, 5);
        const maxAbs = Math.max(1, ...top.map((c) => Math.abs(c.back_to_back ? 0 : c.total)));
        const rows = top.map((c, i) => {
            const r = byId[c.entry_id] || {};
            const chips = TERM_ORDER.filter((t) => Math.round(c.terms[t]) !== 0 && !(t === 'spacing' && c.back_to_back))
                .map((t) => `<span class="ao-term ${c.terms[t] > 0 ? 'pos' : 'neg'}">${fmtNum(c.terms[t])} ${esc(TERM_LABELS[t])}</span>`);
            if (c.back_to_back) chips.unshift('<span class="ao-term veto">✗ would be twice in a row</span>');
            const ctx = [];
            ctx.push(c.projected_sung === 0 ? 'hasn’t sung yet' : `will have sung ${c.projected_sung}`);
            if (c.projected_wait != null) ctx.push(`would have waited ${Math.round(c.projected_wait)} min`);
            if (c.nearest_gap != null && !c.back_to_back) ctx.push(`${c.nearest_gap} rows from their nearest other song`);
            const width = c.back_to_back ? 0 : Math.max(2, Math.round(100 * Math.max(0, c.total) / maxAbs));
            return `<div class="ao-cand${i === 0 ? ' win' : ''}" style="--h:${singerHue(r.singer)}">
                <div class="ao-cand-top">
                    <span class="ao-cand-name">${i === 0 ? '★ ' : ''}${esc(r.singer)} <span class="ao-song">${esc(r.song)}</span></span>
                    <span class="ao-cand-total">${c.back_to_back ? 'vetoed' : Math.round(c.total).toLocaleString()}</span>
                </div>
                <div class="ao-bar"><span style="width:${width}%"></span></div>
                <div class="ao-cand-terms">${chips.join('') || '<span class="ao-muted">no score terms</span>'}</div>
                <div class="ao-cand-ctx">${esc(ctx.join(' · '))}</div>
            </div>`;
        }).join('');
        const more = cands.length > top.length ? `<p class="ao-muted">+${cands.length - top.length} more contenders scored lower.</p>` : '';
        const only = cands.length === 1 ? '<p class="ao-note">Only one contender was left for this spot.</p>' : '';
        return `<div class="ao-dec">
            <div class="ao-dec-h">Row ${row}: why ${label}?</div>
            <p class="ao-note">The contenders were each singer's next song. This spot comes up in about
            ${Math.round(d.starts_in_minutes)} min. Highest score wins.</p>
            ${only}${rows}${more}
        </div>`;
    }

    window.aoToggleSlot = function (key, row) {
        aoOpenSlot[key] = aoOpenSlot[key] === row ? null : row;
        const p = key === 'live' ? aoData.live : aoData.scenarios.find((s) => 'scn:' + s.id === key);
        if (!p) return;
        setHues(p.before);
        const ba = document.querySelector(`.ao-ba[data-key="${CSS.escape(key)}"]`);
        if (ba) {
            ba.querySelectorAll('.ao-col:last-child .ao-row').forEach((el, i) => {
                el.classList.toggle('ao-open', aoOpenSlot[key] === i + 1);
            });
        }
        const dec = document.querySelector(`.ao-decision[data-key="${CSS.escape(key)}"]`);
        if (dec) {
            dec.innerHTML = aoOpenSlot[key] ? renderDecision(p, aoOpenSlot[key]) : '';
            if (aoOpenSlot[key]) dec.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    };
})();
