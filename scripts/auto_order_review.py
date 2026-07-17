#!/usr/bin/env python3
"""Generate a standalone review web UI for the Auto Order algorithm.

Auto-selects ~10 diverse, realistic rotation scenarios reconstructed from real
past events (plus a couple of crisp synthetic edge cases), runs the algorithm on
each, and writes a self-contained HTML page showing BEFORE | AFTER side-by-side
with per-scenario feedback fields and an "export feedback" button.

Nothing here touches production — it reads a read-only DB snapshot only.

Usage:
    python3 scripts/auto_order_review.py                    # writes + opens the page
    python3 scripts/auto_order_review.py --db /tmp/snap.db --out /tmp/review.html
    python3 scripts/auto_order_review.py --no-open
"""

import argparse
import html
import json
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kj-controller"))

from auto_order import AutoOrderConfig, compute_auto_order, _locked_indices  # noqa: E402
from auto_order_sim import (  # noqa: E402
    reconstruct_real_scenario, synthetic_scenarios,
)

DEFAULT_DB = "/tmp/nomadpc_rotation_snapshot.db"
DEFAULT_OUT = "/tmp/auto_order_review.html"


# ---------------------------------------------------------------------------
# Scenario features + diverse selection
# ---------------------------------------------------------------------------

def _features(before, result, config):
    tail = before[config.lock_head:]
    from collections import Counter
    owner_counts = Counter(e.owner for e in tail)
    max_burst = max(owner_counts.values()) if owner_counts else 0
    return {
        "n": len(before),
        "new_in_backlog": sum(1 for e in tail if e.sung == 0),
        "max_burst": max_burst,
        "max_wait": max((e.wait_minutes or 0) for e in before) if before else 0,
        "inv_before": result.metrics_before["fairness_inversions"],
        "inv_after": result.metrics_after["fairness_inversions"],
        "b2b_before": result.metrics_before["back_to_back"],
        "b2b_after": result.metrics_after["back_to_back"],
        "n_moved": sum(1 for p in result.placements if p.moved),
        "changed": result.changed,
    }


# Each category: (title, predicate(features) -> bool, priority-score(features) -> float).
# Predicates/scorers use ONLY input (BEFORE-state) features so the selected set is
# stable across algorithm changes — you re-review the same scenarios each round.
_CATEGORIES = [
    ("New singer waiting behind repeat singers",
     lambda f: f["new_in_backlog"] >= 1 and f["n"] >= 12,
     lambda f: f["inv_before"] + f["max_wait"] / 30.0),
    ("A singer's burst of songs, spread out",
     lambda f: f["max_burst"] >= 4,
     lambda f: f["max_burst"]),
    ("Fairness fix — fewer-sung singers moved up",
     lambda f: f["inv_before"] >= 12,
     lambda f: f["inv_before"]),
    ("Someone waiting a long time jumps ahead",
     lambda f: f["max_wait"] >= 90,
     lambda f: f["max_wait"]),
    ("Busy mixed backlog",
     lambda f: f["n"] >= 16,
     lambda f: f["n"]),
    ("Small / early-night pool",
     lambda f: 6 <= f["n"] <= 9,
     lambda f: -f["n"]),
    ("Already fair — Auto Order changes little",
     lambda f: f["inv_before"] <= 6 and f["b2b_before"] <= 1 and f["n"] >= 10,
     lambda f: -f["inv_before"]),
]


def collect_real_candidates(db_path, config):
    conn = sqlite3.connect(db_path)
    nights = [r[0] for r in conn.execute(
        "SELECT night_date FROM rotation_archive GROUP BY night_date "
        "HAVING COUNT(*) > 25 ORDER BY night_date DESC")]
    conn.close()

    candidates = []
    for night in nights:
        for pidx in range(8, 80, 4):
            before = reconstruct_real_scenario(db_path, night, pidx)
            if not before:
                continue
            result = compute_auto_order(before, config)
            f = _features(before, result, config)
            candidates.append({
                "night": night, "pidx": pidx,
                "before": before, "result": result, "features": f,
            })
    return candidates


def select_diverse(candidates, want=8, max_per_night=2):
    """Pick a diverse set covering as many categories as possible."""
    chosen = []
    used_keys = set()
    per_night = {}

    for title, pred, scorer in _CATEGORIES:
        pool = [c for c in candidates
                if (c["night"], c["pidx"]) not in used_keys
                and per_night.get(c["night"], 0) < max_per_night
                and pred(c["features"])]
        if not pool:
            continue
        best = max(pool, key=lambda c: scorer(c["features"]))
        best["category"] = title
        chosen.append(best)
        used_keys.add((best["night"], best["pidx"]))
        per_night[best["night"]] = per_night.get(best["night"], 0) + 1
        if len(chosen) >= want:
            break

    # Backfill with the busiest remaining scenarios for variety (input-only key).
    if len(chosen) < want:
        rest = sorted(
            (c for c in candidates if (c["night"], c["pidx"]) not in used_keys),
            key=lambda c: (c["features"]["n"], c["features"]["inv_before"]), reverse=True)
        for c in rest:
            if per_night.get(c["night"], 0) >= max_per_night:
                continue
            c["category"] = "General reordering"
            chosen.append(c)
            used_keys.add((c["night"], c["pidx"]))
            per_night[c["night"]] = per_night.get(c["night"], 0) + 1
            if len(chosen) >= want:
                break
    return chosen


# ---------------------------------------------------------------------------
# Serialise scenarios to a JSON-friendly shape for the page
# ---------------------------------------------------------------------------

def _tier(sung):
    if sung == 0:
        return "NEW"
    if sung > 5:
        return f"{sung}× heavy"
    return f"{sung}×"


def _row(e):
    return {
        "id": e.id,
        "singer": html.escape(e.singer or ""),
        "song": html.escape(e.song_artist or ""),
        "sung": e.sung, "tier": _tier(e.sung), "wait": e.wait_minutes,
    }


def scenario_to_dict(title, subtitle, before, result, config):
    before_index = {e.id: i for i, e in enumerate(before)}
    locked = sorted(_locked_indices(before, config))
    after_rows = []
    reason_by_id = {p.entry.id: p.reason for p in result.placements}
    for i, e in enumerate(result.order):
        old = before_index.get(e.id)
        move = "same"
        if old is None:
            move = "same"
        elif old > i:
            move = "up"
        elif old < i:
            move = "down"
        r = _row(e)
        r["move"] = move
        r["reason"] = html.escape(reason_by_id.get(e.id, ""))
        after_rows.append(r)
    return {
        "title": title,
        "subtitle": subtitle,
        "locked": locked,   # 0-based indices that stay fixed (rows 1-3 + un-bumped 4/5)
        "before": [_row(e) for e in before],
        "after": after_rows,
        "metrics_before": result.metrics_before,
        "metrics_after": result.metrics_after,
    }


def build_scenarios(db_path, config):
    scenarios = []

    # Synthetic edge cases first — crisp, labelled.
    for title, note, before in synthetic_scenarios():
        result = compute_auto_order(before, config)
        scenarios.append(scenario_to_dict(
            f"[edge case] {title}", note, before, result, config))

    # Real reconstructed scenarios.
    if os.path.exists(db_path):
        cands = collect_real_candidates(db_path, config)
        for c in select_diverse(cands, want=8):
            sub = (f"Reconstructed from {c['night']} (partway through the night). "
                   f"BEFORE = raw backlog as submitted; AFTER = Auto Order.")
            scenarios.append(scenario_to_dict(
                f"{c['category']} — {c['night']}", sub, c["before"], c["result"], config))
    return scenarios


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

PAGE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Auto Order — scenario review</title>
<style>
:root {{
  --bg:#0e1116; --panel:#171b22; --panel2:#1e232c; --line:#2a313c;
  --text:#e6e9ef; --muted:#8b95a5; --pink:#ff2d78; --green:#38d39f;
  --amber:#f0a83c; --red:#ff5c5c; --up:#38d39f; --down:#6ea8ff;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text);
  font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
header {{ position:sticky; top:0; z-index:10; background:#0b0e13ee;
  backdrop-filter:blur(6px); border-bottom:1px solid var(--line);
  padding:14px 20px; display:flex; align-items:center; gap:16px; flex-wrap:wrap; }}
header h1 {{ font-size:18px; margin:0; color:var(--pink); }}
header .sub {{ color:var(--muted); font-size:13px; }}
header .spacer {{ flex:1; }}
button {{ background:var(--panel2); color:var(--text); border:1px solid var(--line);
  border-radius:8px; padding:8px 14px; cursor:pointer; font-size:13px; }}
button:hover {{ border-color:var(--pink); }}
button.primary {{ background:var(--pink); border-color:var(--pink); color:#fff; font-weight:600; }}
main {{ padding:20px; max-width:1180px; margin:0 auto; }}
.scenario {{ background:var(--panel); border:1px solid var(--line); border-radius:14px;
  margin-bottom:22px; overflow:hidden; }}
.scenario-head {{ padding:14px 18px; border-bottom:1px solid var(--line); }}
.scenario-head h2 {{ margin:0 0 4px; font-size:16px; }}
.scenario-head .desc {{ color:var(--muted); font-size:13px; }}
.cols {{ display:grid; grid-template-columns:1fr 1fr; gap:0; }}
.col {{ padding:14px 16px; min-width:0; }}   /* min-width:0 lets children ellipsis */
.col.before {{ border-right:1px solid var(--line); }}
.col h3 {{ margin:0 0 10px; font-size:12px; letter-spacing:.06em; text-transform:uppercase;
  color:var(--muted); }}
.row {{ display:flex; align-items:center; gap:8px; padding:5px 8px; border-radius:8px;
  margin-bottom:3px; background:var(--panel2); min-width:0; }}
.row.locked {{ background:#14251c; border:1px solid #1f4030; }}
.row.moved-up {{ background:#12291f; }}
.row.moved-down {{ background:#141c2b; }}
.pos {{ width:26px; text-align:right; color:var(--muted); font-variant-numeric:tabular-nums; }}
.arrow {{ width:16px; text-align:center; font-weight:700; }}
.arrow.up {{ color:var(--up); }} .arrow.down {{ color:var(--down); }} .arrow.same {{ color:var(--muted); }}
.lockicon {{ width:16px; text-align:center; }}
.singer {{ font-weight:600; min-width:96px; max-width:120px; overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap; }}
.badge {{ font-size:11px; padding:1px 7px; border-radius:20px; white-space:nowrap; }}
.badge.new {{ background:#123a2a; color:var(--green); border:1px solid #1f6b4c; }}
.badge.norm {{ background:#22262f; color:var(--muted); }}
.badge.heavy {{ background:#3a2312; color:var(--amber); border:1px solid #6b451f; }}
.wait {{ font-size:11px; padding:1px 7px; border-radius:20px; background:#22262f; color:var(--muted);
  white-space:nowrap; font-variant-numeric:tabular-nums; }}
.wait.long {{ background:#3a1212; color:var(--red); border:1px solid #6b1f1f; }}
.song {{ color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  flex:1; min-width:0; }}
.metrics {{ display:flex; gap:16px; padding:10px 18px; border-top:1px solid var(--line);
  color:var(--muted); font-size:12px; flex-wrap:wrap; }}
.metrics b {{ color:var(--text); font-weight:600; }}
.better {{ color:var(--green); }} .worse {{ color:var(--red); }}
.feedback {{ padding:14px 18px; border-top:1px solid var(--line); background:#12151b; }}
.feedback .verdict {{ display:flex; gap:8px; margin-bottom:8px; }}
.feedback .verdict label {{ display:flex; align-items:center; gap:6px; cursor:pointer;
  padding:5px 12px; border:1px solid var(--line); border-radius:8px; font-size:13px; }}
.feedback textarea {{ width:100%; min-height:52px; background:var(--panel2); color:var(--text);
  border:1px solid var(--line); border-radius:8px; padding:9px; font:13px inherit; resize:vertical; }}
dialog {{ background:var(--panel); color:var(--text); border:1px solid var(--line);
  border-radius:12px; width:min(760px,92vw); padding:0; }}
dialog .dhead {{ padding:14px 18px; border-bottom:1px solid var(--line); display:flex; }}
dialog textarea {{ width:100%; min-height:340px; border:0; background:var(--bg); color:var(--text);
  padding:14px; font:12px/1.5 ui-monospace,Menlo,monospace; resize:vertical; }}
</style></head><body>
<header>
  <h1>Auto Order</h1>
  <span class="sub">scenario review &mdash; {count} cases &middot; BEFORE = as submitted, AFTER = what Auto Order would do</span>
  <span class="spacer"></span>
  <button onclick="collapseAll()">Collapse metrics</button>
  <button class="primary" onclick="exportFeedback()">Export my feedback</button>
</header>
<main id="app"></main>
<dialog id="exportDlg">
  <div class="dhead"><b>Copy this and paste it back to Claude</b><span style="flex:1"></span>
    <button onclick="document.getElementById('exportDlg').close()">Close</button></div>
  <textarea id="exportText" readonly></textarea>
</dialog>
<script>
const SCENARIOS = {scenarios_json};

function badge(tier, sung) {{
  let cls = 'norm';
  if (sung === 0) cls = 'new';
  else if (sung > 5) cls = 'heavy';
  return `<span class="badge ${{cls}}">${{tier}}</span>`;
}}
function waitPill(w) {{
  if (w === null || w === undefined) return `<span class="wait long">&infin;</span>`;
  const cls = w > 60 ? 'wait long' : 'wait';
  return `<span class="${{cls}}">${{w}}m</span>`;
}}
function rowHtml(r, idx, side, lockedSet) {{
  const locked = lockedSet.has(idx);
  let cls = 'row';
  let arrow = '';
  if (side === 'after') {{
    if (locked) {{ cls += ' locked'; arrow = `<span class="lockicon">🔒</span>`; }}
    else if (r.move === 'up') {{ cls += ' moved-up'; arrow = `<span class="arrow up">↑</span>`; }}
    else if (r.move === 'down') {{ cls += ' moved-down'; arrow = `<span class="arrow down">↓</span>`; }}
    else arrow = `<span class="arrow same">·</span>`;
  }} else {{
    if (locked) {{ cls += ' locked'; arrow = `<span class="lockicon">🔒</span>`; }}
    else arrow = `<span class="arrow same"></span>`;
  }}
  const title = r.reason ? ` title="${{r.reason}}"` : '';
  return `<div class="${{cls}}"${{title}}>${{arrow}}<span class="pos">${{idx+1}}</span>`
    + `<span class="singer">${{r.singer}}</span>${{badge(r.tier, r.sung)}}${{waitPill(r.wait)}}`
    + `<span class="song">${{r.song||''}}</span></div>`;
}}
function delta(before, after, lowerBetter=true) {{
  if (before === after) return `<b>${{after}}</b>`;
  const improved = lowerBetter ? after < before : after > before;
  const cls = improved ? 'better' : 'worse';
  return `${{before}} &rarr; <b class="${{cls}}">${{after}}</b>`;
}}
function metricsHtml(s) {{
  const b = s.metrics_before, a = s.metrics_after;
  return `<div class="metrics">`
    + `<span>same singer twice in a row: ${{delta(b.back_to_back, a.back_to_back)}}</span>`
    + `<span>fairness out-of-order pairs: ${{delta(b.fairness_inversions, a.fairness_inversions)}}</span>`
    + `<span>projected to wait &gt;1hr: ${{delta(b.projected_over_hour, a.projected_over_hour)}}</span>`
    + `<span>worst projected wait: ${{delta(b.worst_projected_wait, a.worst_projected_wait)}}m</span>`
    + `<span>median gap between a singer's songs: <b>${{a.median_repeat_gap ?? '—'}}</b></span>`
    + `</div>`;
}}
function render() {{
  const app = document.getElementById('app');
  app.innerHTML = SCENARIOS.map((s, i) => {{
    const lockedSet = new Set(s.locked || []);
    return `
    <div class="scenario" data-i="${{i}}">
      <div class="scenario-head"><h2>${{i+1}}. ${{s.title}}</h2>
        <div class="desc">${{s.subtitle||''}}</div></div>
      <div class="cols">
        <div class="col before"><h3>Before</h3>
          ${{s.before.map((r,idx)=>rowHtml(r,idx,'before',lockedSet)).join('')}}</div>
        <div class="col after"><h3>After &mdash; Auto Order</h3>
          ${{s.after.map((r,idx)=>rowHtml(r,idx,'after',lockedSet)).join('')}}</div>
      </div>
      ${{metricsHtml(s)}}
      <div class="feedback">
        <div class="verdict">
          <label><input type="radio" name="v${{i}}" value="correct"> ✅ Looks right</label>
          <label><input type="radio" name="v${{i}}" value="wrong"> ⚠️ Not what I'd do</label>
        </div>
        <textarea id="note${{i}}" placeholder="Optional: what would you change about this one?"></textarea>
      </div>
    </div>`;
  }}).join('');
}}
function collapseAll() {{
  document.querySelectorAll('.metrics').forEach(m => m.style.display =
    m.style.display === 'none' ? 'flex' : 'none');
}}
function exportFeedback() {{
  let out = 'AUTO ORDER — REVIEW FEEDBACK\\n============================\\n\\n';
  SCENARIOS.forEach((s, i) => {{
    const v = document.querySelector(`input[name="v${{i}}"]:checked`);
    const note = document.getElementById('note'+i).value.trim();
    if (!v && !note) return;
    out += `#${{i+1}} ${{s.title}}\\n`;
    out += `  verdict: ${{v ? v.value : '(none)'}}\\n`;
    if (note) out += `  note: ${{note}}\\n`;
    out += '\\n';
  }});
  if (out.indexOf('#') === -1) out += '(no feedback entered yet)\\n';
  document.getElementById('exportText').value = out;
  const dlg = document.getElementById('exportDlg');
  dlg.showModal();
  document.getElementById('exportText').select();
}}
render();
</script>
</body></html>
"""


def render_page(scenarios):
    return PAGE_TEMPLATE.format(
        count=len(scenarios),
        scenarios_json=json.dumps(scenarios),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    config = AutoOrderConfig()
    scenarios = build_scenarios(args.db, config)
    page = render_page(scenarios)
    with open(args.out, "w") as f:
        f.write(page)
    print(f"Wrote {len(scenarios)} scenarios to {args.out}")
    if not args.no_open:
        try:
            subprocess.run(["open", args.out], check=False)
        except Exception:
            pass


if __name__ == "__main__":
    main()
