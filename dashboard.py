"""
dashboard.py — Generates an HTML dashboard with:
  - Daily / monthly PnL
  - Round-robin group standings by day/block
  - Full signal history

Portfolio / showcase version: the position-based analysis and the
strategy-filter ("Pivot & Stake") tab have been removed, since they exist
specifically to test the proprietary signal rule. Everything here is
strategy-agnostic — it works the same regardless of what generates a signal.

Usage:
  python dashboard.py             # generate and open dashboard.html
  python dashboard.py --no-open   # generate without opening
"""

import json
import csv
import os
import webbrowser
import argparse
from datetime import datetime, date
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
MATCHES_JSON = os.path.join(HERE, "matches_db.json")
SIGNALS_CSV  = os.path.join(HERE, "signals_db.csv")
OUTPUT_HTML  = os.path.join(HERE, "dashboard.html")

BLOCK_HISTORY = {
    10: [(8,0),(8,30),(9,0),(9,30)],
    14: [(12,0),(12,30),(13,0),(13,30)],
    18: [(16,0),(16,30),(17,0),(17,30)],
    22: [(20,0),(20,30),(21,0),(21,30)],
}


def load_matches():
    if not os.path.exists(MATCHES_JSON):
        return {}
    with open(MATCHES_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def load_signals():
    if not os.path.exists(SIGNALS_CSV):
        return []
    with open(SIGNALS_CSV, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_standings(matches):
    """Group players into round-robin groups (union-find on who played whom) and rank them."""
    parent = {}
    def find(x):
        if x not in parent: parent[x] = x
        if parent[x] != x: parent[x] = find(parent[x])
        return parent[x]
    def union(x, y):
        px, py = find(x), find(y)
        if px != py: parent[px] = py

    for m in matches:
        hid, aid = m.get("home_id"), m.get("away_id")
        if hid and aid: union(hid, aid)

    records = defaultdict(dict)
    for m in matches:
        hid, aid = m["home_id"], m["away_id"]
        hs, as_ = m["home_sets"], m["away_sets"]
        hw, aw = (1,0) if hs > as_ else ((0,1) if as_ > hs else (0,0))
        gk = find(hid)
        for pid, pname, w, l, sf, sa in [
            (hid, m["home_name"], hw, aw, hs, as_),
            (aid, m["away_name"], aw, hw, as_, hs),
        ]:
            if pid not in records[gk]:
                records[gk][pid] = {"player_id":pid,"player_name":pname,"wins":0,"losses":0,"sets_won":0,"sets_lost":0}
            records[gk][pid]["wins"] += w
            records[gk][pid]["losses"] += l
            records[gk][pid]["sets_won"] += sf
            records[gk][pid]["sets_lost"] += sa

    return {gk: sorted(p.values(), key=lambda r: (-r["wins"],-r["sets_won"],-(r["sets_won"]-r["sets_lost"])))
            for gk, p in records.items()}


def generate_data():
    db = load_matches()
    signals = load_signals()

    # === PnL ===
    pnl_by_day = defaultdict(lambda: {"w":0,"l":0,"pnl":0.0,"bets":0})
    pnl_monthly = defaultdict(lambda: {"w":0,"l":0,"pnl":0.0,"bets":0})

    for s in signals:
        d = s.get("date","")
        month = d[:7] if len(d) >= 7 else ""
        pnl_val = float(s.get("pnl") or 0)
        result = s.get("result","")
        pnl_by_day[d]["bets"] += 1
        if month:
            pnl_monthly[month]["bets"] += 1
        if result == "W":
            pnl_by_day[d]["w"] += 1
            pnl_by_day[d]["pnl"] += pnl_val
            if month: pnl_monthly[month]["w"] += 1; pnl_monthly[month]["pnl"] += pnl_val
        elif result == "L":
            pnl_by_day[d]["l"] += 1
            pnl_by_day[d]["pnl"] += pnl_val
            if month: pnl_monthly[month]["l"] += 1; pnl_monthly[month]["pnl"] += pnl_val

    # === Standings by day/block ===
    standings_data = {}
    for day_str, day_events in sorted(db.items()):
        standings_data[day_str] = {}
        for block_h, hist_times in BLOCK_HISTORY.items():
            target_set = set(hist_times)
            block_matches = [
                m for m in day_events.values()
                if m.get("status") == "finished"
                and not m.get("_baseline", False)
                and (m.get("hour"), m.get("minute")) in target_set
            ]
            if block_matches:
                st = build_standings(block_matches)
                standings_data[day_str][f"{block_h:02d}:00"] = st

    return {
        "pnl_by_day": dict(pnl_by_day),
        "pnl_monthly": dict(pnl_monthly),
        "standings": standings_data,
        "signals": signals,
        "generated": datetime.now().isoformat(),
    }


def generate_html(data):
    signals_json = json.dumps(data["signals"], default=str)
    pnl_day_json = json.dumps(data["pnl_by_day"], default=str)
    pnl_month_json = json.dumps(data["pnl_monthly"], default=str)
    standings_json = json.dumps(data["standings"], default=str)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Signal Bot — Dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#09090b;color:#e4e4e7;min-height:100vh}}
.container{{max-width:1200px;margin:0 auto;padding:20px}}
.hdr{{display:flex;justify-content:space-between;align-items:center;padding:20px 0;border-bottom:1px solid #27272a;margin-bottom:24px}}
.hdr h1{{font-size:20px;font-weight:600;color:#fafafa}}
.hdr .sub{{font-size:12px;color:#71717a;margin-top:2px}}
.hdr .ts{{font-size:11px;color:#52525b;text-align:right}}
.tabs{{display:flex;gap:4px;margin-bottom:24px;border-bottom:1px solid #27272a;padding-bottom:12px}}
.tab{{padding:8px 20px;font-size:13px;border:1px solid #27272a;border-radius:6px;cursor:pointer;background:transparent;color:#a1a1aa;font-family:inherit;transition:all .15s}}
.tab.on{{background:#18181b;color:#4ade80;border-color:#4ade80}}
.tab:hover:not(.on){{border-color:#3f3f46;color:#d4d4d8}}
.sec{{display:none}}.sec.on{{display:block}}
.grid4{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}}
.kpi{{background:#18181b;border:1px solid #27272a;border-radius:10px;padding:16px}}
.kpi .lbl{{font-size:11px;color:#71717a;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}}
.kpi .val{{font-size:26px;font-weight:700}}
.kpi .val.g{{color:#4ade80}}.kpi .val.r{{color:#f87171}}.kpi .val.b{{color:#60a5fa}}
.kpi .sub{{font-size:11px;color:#52525b;margin-top:4px}}
.card{{background:#18181b;border:1px solid #27272a;border-radius:10px;padding:16px;margin-bottom:16px}}
.card h3{{font-size:14px;font-weight:600;color:#d4d4d8;margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.card h3 .dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:left;padding:8px 10px;color:#71717a;font-weight:500;border-bottom:1px solid #27272a;text-transform:uppercase;letter-spacing:.06em;font-size:10px}}
td{{padding:7px 10px;border-bottom:1px solid #1c1c1e;color:#d4d4d8}}
tr:hover td{{background:#1c1c1e}}
.badge{{display:inline-block;font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px;letter-spacing:.04em}}
.badge.w{{background:#052e16;color:#4ade80}}.badge.l{{background:#450a0a;color:#f87171}}
.badge.open{{background:#1e1b4b;color:#818cf8}}
.bar-row{{display:flex;align-items:center;gap:10px;margin-bottom:8px}}
.bar-lbl{{font-size:12px;color:#71717a;width:70px;text-align:right}}
.bar-wrap{{flex:1;height:22px;background:#1c1c1e;border-radius:4px;overflow:hidden;border:1px solid #27272a}}
.bar-fill{{height:100%;border-radius:3px;transition:width .3s}}
.bar-val{{font-size:12px;font-weight:600;width:65px}}
.filter-row{{display:flex;gap:8px;align-items:center;margin-bottom:16px;flex-wrap:wrap}}
.filter-row select,.filter-row input{{font-family:inherit;font-size:12px;background:#18181b;color:#d4d4d8;border:1px solid #27272a;border-radius:6px;padding:6px 10px}}
.filter-row label{{font-size:12px;color:#71717a}}
.refresh-btn{{background:#4ade80;color:#000;border:none;font-family:inherit;font-size:12px;font-weight:700;padding:8px 18px;border-radius:6px;cursor:pointer}}
.refresh-btn:hover{{background:#22c55e}}
.empty{{text-align:center;padding:40px;color:#3f3f46;font-size:13px}}
</style>
</head>
<body>
<div class="container">

<div class="hdr">
  <div>
    <h1>Signal Bot — Dashboard</h1>
    <div class="sub">PnL tracking · Signal history · Standings</div>
  </div>
  <div class="ts">Generated: {data['generated'][:16].replace('T',' ')}<br>
    <button class="refresh-btn" onclick="location.reload()" style="margin-top:6px">Refresh</button>
  </div>
</div>

<div class="tabs">
  <button class="tab on" onclick="sw('pnl',this)">P&L</button>
  <button class="tab" onclick="sw('standings',this)">Standings</button>
  <button class="tab" onclick="sw('signals',this)">Signal History</button>
  <button class="tab" onclick="sw('admin',this)">DB Admin</button>
</div>

<!-- PNL TAB -->
<div id="s-pnl" class="sec on">
  <div class="grid4" id="kpi-row"></div>
  <div class="card"><h3>Monthly P&L</h3><div id="pnl-monthly"></div></div>
  <div class="card"><h3>Daily P&L</h3><div id="pnl-daily"></div></div>
</div>

<!-- STANDINGS TAB -->
<div id="s-standings" class="sec">
  <div class="filter-row">
    <label>Day:</label>
    <select id="day-filter" onchange="onDayChange()"></select>
    <label>Block:</label>
    <select id="block-filter" onchange="renderStandings()"></select>
  </div>
  <div id="standings-container"></div>
</div>

<!-- SIGNALS TAB -->
<div id="s-signals" class="sec">
  <div class="card">
    <h3>Full signal history</h3>
    <div style="overflow-x:auto"><table id="sig-table">
      <thead><tr>
        <th>Date</th><th>Time</th><th>Player</th><th>Opponent</th>
        <th>Record</th><th>Opp. record</th><th>Odds</th><th>Stake</th>
        <th>Result</th><th>P&L</th><th>Balance</th>
      </tr></thead>
      <tbody id="sig-body"></tbody>
    </table></div>
  </div>
</div>

<!-- ADMIN TAB -->
<div id="s-admin" class="sec">
  <div class="card" id="admin-banner-static" style="display:none;border-color:#facc15;background:#1c1a07">
    <h3 style="color:#facc15">⚠ Static file mode</h3>
    <p style="font-size:13px;color:#d4d4d8;line-height:1.6">
      You're viewing this dashboard as an HTML file opened directly in the browser.
      Edit/delete buttons are <strong>disabled</strong> in this mode.<br><br>
      To enable them, run the Flask server:
    </p>
    <pre style="background:#09090b;border:1px solid #27272a;padding:10px;border-radius:6px;font-size:12px;color:#4ade80;margin-top:8px">python dashboard_server.py
# then open http://localhost:5000</pre>
  </div>

  <div class="card" id="admin-banner-live" style="display:none;border-color:#4ade80;background:#062a14">
    <h3 style="color:#4ade80">● Live mode (Flask)</h3>
    <p style="font-size:12px;color:#a1a1aa">
      Edit and delete are active. Changes update the CSV and automatically recalculate
      PnL and running balance.
    </p>
  </div>

  <div class="card">
    <h3>Signal management</h3>
    <div class="filter-row" style="margin-bottom:12px">
      <label>Search player:</label>
      <input type="text" id="admin-search" placeholder="e.g. Smith" oninput="renderAdminTable()" style="min-width:180px">
      <label>Filter result:</label>
      <select id="admin-filter-result" onchange="renderAdminTable()">
        <option value="">All</option>
        <option value="open">Open only</option>
        <option value="W">W only</option>
        <option value="L">L only</option>
        <option value="VOID">VOID only</option>
      </select>
      <button class="refresh-btn" onclick="reloadFromBackend()">🔄 Reload from DB</button>
    </div>

    <div style="overflow-x:auto;max-height:600px;overflow-y:auto">
      <table id="admin-table">
        <thead><tr>
          <th>#</th><th>Date</th><th>Time</th><th>Player</th><th>Opp.</th>
          <th>Odds</th><th>Stake</th><th>Result</th><th>P&L</th><th>Bal</th><th>Notes</th><th>Actions</th>
        </tr></thead>
        <tbody id="admin-body"></tbody>
      </table>
    </div>
    <div id="admin-count" style="font-size:11px;color:#71717a;margin-top:8px"></div>
  </div>
</div>

<!-- EDIT MODAL -->
<div id="edit-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.75);z-index:1000;align-items:center;justify-content:center">
  <div style="background:#18181b;border:1px solid #27272a;border-radius:10px;padding:24px;max-width:500px;width:90%">
    <h3 style="font-size:15px;color:#fafafa;margin-bottom:16px">Edit signal <span id="edit-title" style="color:#71717a;font-weight:400"></span></h3>

    <div style="display:grid;gap:12px;font-size:13px">
      <div>
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">RESULT</label>
        <select id="edit-result" style="width:100%">
          <option value="">— open —</option>
          <option value="W">W (won)</option>
          <option value="L">L (lost)</option>
          <option value="VOID">VOID</option>
        </select>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div>
          <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">ODDS</label>
          <input type="number" id="edit-odds" step="0.01" min="1" style="width:100%">
        </div>
        <div>
          <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">STAKE</label>
          <input type="number" id="edit-stake" step="0.5" min="0" style="width:100%">
        </div>
      </div>
      <div>
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">NOTES</label>
        <input type="text" id="edit-notes" style="width:100%">
      </div>
      <div style="font-size:11px;color:#52525b;background:#0f0f12;padding:8px;border-radius:6px;border:1px solid #27272a">
        PnL preview: <strong id="edit-preview" style="color:#fafafa">—</strong>
      </div>
    </div>

    <div style="display:flex;gap:8px;margin-top:20px;justify-content:flex-end">
      <button onclick="closeEditModal()" style="background:transparent;border:1px solid #27272a;color:#a1a1aa;padding:8px 16px;border-radius:6px;font-family:inherit;cursor:pointer">Cancel</button>
      <button onclick="saveEdit()" class="refresh-btn">💾 Save</button>
    </div>
  </div>
</div>

</div>

<script>
const SIGNALS = {signals_json};
const PNL_DAY = {pnl_day_json};
const PNL_MONTH = {pnl_month_json};
const STANDINGS = {standings_json};

function sw(id, el) {{
  document.querySelectorAll('.sec').forEach(s=>s.classList.remove('on'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
  document.getElementById('s-'+id).classList.add('on');
  el.classList.add('on');
}}

// === KPIs ===
function renderKPIs() {{
  const settled = SIGNALS.filter(s=>s.result==='W'||s.result==='L');
  const wins = settled.filter(s=>s.result==='W').length;
  const losses = settled.length - wins;
  const totalPnl = settled.reduce((s,r)=>s+parseFloat(r.pnl||0),0);
  const wr = settled.length ? Math.round(wins/settled.length*100) : 0;
  const openCount = SIGNALS.filter(s=>!s.result).length;

  const today = new Date().toISOString().slice(0,10);
  const todayData = PNL_DAY[today] || {{w:0,l:0,pnl:0,bets:0}};
  const todayPnl = parseFloat(todayData.pnl||0);

  document.getElementById('kpi-row').innerHTML = `
    <div class="kpi"><div class="lbl">Total P&L</div><div class="val ${{totalPnl>=0?'g':'r'}}">${{totalPnl>=0?'+':''}}${{totalPnl.toFixed(2)}}€</div><div class="sub">${{settled.length}} closed bets</div></div>
    <div class="kpi"><div class="lbl">Win rate</div><div class="val ${{wr>=54?'g':'r'}}">${{wr}}%</div><div class="sub">${{wins}}W / ${{losses}}L</div></div>
    <div class="kpi"><div class="lbl">P&L today</div><div class="val ${{todayPnl>=0?'g':'r'}}">${{todayPnl>=0?'+':''}}${{todayPnl.toFixed(2)}}€</div><div class="sub">${{todayData.bets||0}} bets</div></div>
    <div class="kpi"><div class="lbl">Open signals</div><div class="val b">${{openCount}}</div><div class="sub">${{SIGNALS.length}} total</div></div>
  `;
}}

// === PNL Monthly ===
function renderMonthly() {{
  const months = Object.entries(PNL_MONTH).sort((a,b)=>a[0].localeCompare(b[0]));
  if (!months.length) {{ document.getElementById('pnl-monthly').innerHTML='<div class="empty">No data</div>'; return; }}
  const maxAbs = Math.max(...months.map(([,v])=>Math.abs(parseFloat(v.pnl||0))), 1);
  document.getElementById('pnl-monthly').innerHTML = months.map(([m,v]) => {{
    const pnl = parseFloat(v.pnl||0);
    const pct = Math.round(Math.abs(pnl)/maxAbs*100);
    const col = pnl>=0 ? '#4ade80':'#f87171';
    const bg = pnl>=0 ? '#052e16':'#450a0a';
    const wr = v.w+v.l > 0 ? Math.round(v.w/(v.w+v.l)*100) : 0;
    return `<div class="bar-row"><span class="bar-lbl">${{m}}</span><div class="bar-wrap"><div class="bar-fill" style="width:${{pct}}%;background:${{bg}};border-right:3px solid ${{col}}"></div></div><span class="bar-val" style="color:${{col}}">${{pnl>=0?'+':''}}${{pnl.toFixed(2)}}€</span><span style="font-size:11px;color:#52525b">${{v.w}}W ${{v.l}}L (${{wr}}%)</span></div>`;
  }}).join('');
}}

// === PNL Daily ===
function renderDaily() {{
  const days = Object.entries(PNL_DAY).sort((a,b)=>b[0].localeCompare(a[0]));
  if (!days.length) {{ document.getElementById('pnl-daily').innerHTML='<div class="empty">No data</div>'; return; }}
  const maxAbs = Math.max(...days.map(([,v])=>Math.abs(parseFloat(v.pnl||0))), 1);
  let running = 0;
  const sorted = [...days].reverse();
  const cumulative = {{}};
  sorted.forEach(([d,v]) => {{ running += parseFloat(v.pnl||0); cumulative[d] = running; }});

  document.getElementById('pnl-daily').innerHTML = days.map(([d,v]) => {{
    const pnl = parseFloat(v.pnl||0);
    const pct = Math.round(Math.abs(pnl)/maxAbs*100);
    const col = pnl>=0?'#4ade80':'#f87171';
    const bg = pnl>=0?'#052e16':'#450a0a';
    const wr = v.w+v.l>0 ? Math.round(v.w/(v.w+v.l)*100) : 0;
    const cum = cumulative[d]||0;
    return `<div class="bar-row"><span class="bar-lbl">${{d.slice(5)}}</span><div class="bar-wrap"><div class="bar-fill" style="width:${{pct}}%;background:${{bg}};border-right:3px solid ${{col}}"></div></div><span class="bar-val" style="color:${{col}}">${{pnl>=0?'+':''}}${{pnl.toFixed(2)}}€</span><span style="font-size:11px;color:#52525b">${{v.w}}W${{v.l}}L (${{wr}}%)</span><span style="font-size:11px;color:${{cum>=0?'#4ade80':'#f87171'}};margin-left:8px">cum: ${{cum>=0?'+':''}}${{cum.toFixed(2)}}</span></div>`;
  }}).join('');
}}

// === Standings ===
function populateDays() {{
  const daySelect = document.getElementById('day-filter');
  const days = Object.keys(STANDINGS).sort().reverse();
  daySelect.innerHTML = days.map(d => `<option value="${{d}}">${{d}}</option>`).join('');
  if (days.length) onDayChange();
}}

function onDayChange() {{
  const day = document.getElementById('day-filter').value;
  const dayData = STANDINGS[day] || {{}};
  const blocks = Object.keys(dayData).sort();
  const blockFilter = document.getElementById('block-filter');
  blockFilter.innerHTML = '<option value="all">All</option>' +
    blocks.map(b => `<option value="${{b}}">${{b}}</option>`).join('');
  renderStandings();
}}

function renderStandings() {{
  const day = document.getElementById('day-filter').value;
  const selectedBlock = document.getElementById('block-filter').value;
  const dayData = STANDINGS[day] || {{}};
  const blocks = Object.keys(dayData).sort();

  const container = document.getElementById('standings-container');
  if (!blocks.length) {{ container.innerHTML='<div class="empty">No data for this day</div>'; return; }}

  const showBlocks = selectedBlock === 'all' ? blocks : [selectedBlock];

  container.innerHTML = showBlocks.filter(b=>dayData[b]).map(block => {{
    const groups = dayData[block];
    return Object.entries(groups).map(([gk, rows]) => {{
      const tbody = rows.map((r,i) => {{
        const diff = r.sets_won - r.sets_lost;
        return `<tr><td>${{i+1}}</td><td>${{r.player_name}}</td><td>${{r.wins}}</td><td>${{r.losses}}</td><td>${{r.sets_won}}</td><td>${{r.sets_lost}}</td><td>${{diff>=0?'+':''}}${{diff}}</td></tr>`;
      }}).join('');
      return `<div class="card"><h3><span class="dot" style="background:#4ade80"></span> Block ${{block}} — Group ${{gk}}</h3>
        <table><thead><tr><th>#</th><th>Player</th><th>W</th><th>L</th><th>Sets+</th><th>Sets-</th><th>Diff</th></tr></thead>
        <tbody>${{tbody}}</tbody></table></div>`;
    }}).join('');
  }}).join('');
}}

// === Signals Table ===
function renderSignals() {{
  const body = document.getElementById('sig-body');
  if (!SIGNALS.length) {{ body.innerHTML='<tr><td colspan="11" style="text-align:center;color:#3f3f46;padding:40px">No signals recorded</td></tr>'; return; }}
  body.innerHTML = [...SIGNALS].reverse().map(s => {{
    const res = s.result || '';
    const badge = res === 'W' ? '<span class="badge w">W</span>' :
                  res === 'L' ? '<span class="badge l">L</span>' :
                  '<span class="badge open">OPEN</span>';
    const pnl = parseFloat(s.pnl||0);
    const pnlStr = s.result ? (pnl>=0?'+':'')+pnl.toFixed(2)+'€' : '—';
    const pnlCol = pnl>=0?'#4ade80':'#f87171';
    const bal = s.balance ? parseFloat(s.balance).toFixed(2)+'€' : '—';
    return `<tr>
      <td>${{s.date||''}}</td><td>${{s.signal_time||''}}</td>
      <td style="font-weight:600">${{s.player||''}}</td><td>${{s.opponent||''}}</td>
      <td>${{s.player_wins||0}}W-${{s.player_losses||0}}L</td>
      <td>${{s.opp_wins||0}}W-${{s.opp_losses||0}}L</td>
      <td>${{s.odds||'—'}}</td><td>€${{s.stake||10}}</td>
      <td>${{badge}}</td>
      <td style="color:${{pnlCol}};font-weight:600">${{pnlStr}}</td>
      <td style="color:#71717a">${{bal}}</td>
    </tr>`;
  }}).join('');
}}

// === DB ADMIN TAB ===
let ADMIN_SIGNALS = SIGNALS.map((s,i)=>({{idx: i+1, ...s}}));
let ADMIN_BACKEND_LIVE = false;
let EDIT_CURRENT_IDX = null;

async function detectBackend(){{
  try {{
    const r = await fetch('/api/health', {{cache:'no-store'}});
    if (r.ok){{
      ADMIN_BACKEND_LIVE = true;
      document.getElementById('admin-banner-live').style.display = 'block';
      document.getElementById('admin-banner-static').style.display = 'none';
    }} else throw new Error('not ok');
  }} catch(e){{
    ADMIN_BACKEND_LIVE = false;
    document.getElementById('admin-banner-static').style.display = 'block';
    document.getElementById('admin-banner-live').style.display = 'none';
  }}
}}

async function reloadFromBackend(){{
  if (!ADMIN_BACKEND_LIVE){{
    alert('Backend unavailable. Run "python dashboard_server.py" and reload the page.');
    return;
  }}
  try {{
    const r = await fetch('/api/signals', {{cache:'no-store'}});
    const data = await r.json();
    ADMIN_SIGNALS = data;
    renderAdminTable();
  }} catch(e){{
    alert('Error loading from backend: ' + e.message);
  }}
}}

function renderAdminTable(){{
  const search = (document.getElementById('admin-search').value||'').toLowerCase();
  const filter = document.getElementById('admin-filter-result').value;
  let filtered = ADMIN_SIGNALS;
  if (search) filtered = filtered.filter(s=>(s.player||'').toLowerCase().includes(search) || (s.opponent||'').toLowerCase().includes(search));
  if (filter === 'open') filtered = filtered.filter(s=>!s.result);
  else if (filter) filtered = filtered.filter(s=>s.result === filter);

  const sorted = [...filtered].sort((a,b)=>{{
    const da = (a.date||'')+(a.signal_time||'');
    const db = (b.date||'')+(b.signal_time||'');
    return db.localeCompare(da);
  }});

  const tbody = document.getElementById('admin-body');
  tbody.innerHTML = sorted.length ? sorted.map(s=>{{
    const result = s.result||'';
    const pnlVal = s.pnl;
    const pnl = pnlVal==='' || pnlVal===null || pnlVal===undefined ? '' : parseFloat(pnlVal);
    const pnlStr = pnl==='' ? '<span style="color:#52525b">—</span>'
                  : (pnl>=0?'<span style="color:#4ade80">+'+pnl.toFixed(2)+'</span>'
                          :'<span style="color:#f87171">'+pnl.toFixed(2)+'</span>');
    const resBadge = result==='W' ? '<span class="badge w">W</span>'
                    : result==='L' ? '<span class="badge l">L</span>'
                    : result==='VOID' ? '<span class="badge" style="background:#3f3f46;color:#a1a1aa">VOID</span>'
                    : '<span class="badge open">OPEN</span>';
    const bal = s.balance||'';
    const disabled = ADMIN_BACKEND_LIVE ? '' : 'disabled style="opacity:0.4;cursor:not-allowed"';
    return `<tr>
      <td style="color:#52525b">${{s.idx}}</td>
      <td>${{s.date||''}}</td><td>${{s.signal_time||''}}</td>
      <td style="font-weight:600">${{s.player||''}}</td>
      <td>${{s.opponent||''}}</td>
      <td>${{s.odds||'—'}}</td>
      <td>${{s.stake||''}}</td>
      <td>${{resBadge}}</td>
      <td>${{pnlStr}}</td>
      <td style="color:#71717a">${{bal}}</td>
      <td style="color:#71717a;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${{s.notes||''}}">${{s.notes||''}}</td>
      <td style="white-space:nowrap">
        <button onclick="openEditModal(${{s.idx}})" ${{disabled}} style="background:transparent;border:1px solid #3f3f46;color:#60a5fa;padding:3px 8px;border-radius:4px;font-family:inherit;font-size:11px;cursor:pointer;margin-right:4px">✏ Edit</button>
        <button onclick="confirmDelete(${{s.idx}})" ${{disabled}} style="background:transparent;border:1px solid #3f3f46;color:#f87171;padding:3px 8px;border-radius:4px;font-family:inherit;font-size:11px;cursor:pointer">🗑</button>
      </td>
    </tr>`;
  }}).join('') : '<tr><td colspan="12" style="text-align:center;color:#3f3f46;padding:30px">No signals</td></tr>';
  document.getElementById('admin-count').textContent = `${{filtered.length}} signals shown (of ${{ADMIN_SIGNALS.length}} total)`;
}}

function openEditModal(idx){{
  if (!ADMIN_BACKEND_LIVE) return;
  EDIT_CURRENT_IDX = idx;
  const s = ADMIN_SIGNALS.find(x=>x.idx===idx);
  if (!s){{ alert('Signal not found'); return; }}
  document.getElementById('edit-title').textContent = `#${{s.idx}} · ${{s.player}} (${{s.date}} ${{s.signal_time}})`;
  document.getElementById('edit-result').value = s.result || '';
  document.getElementById('edit-odds').value = s.odds || '';
  document.getElementById('edit-stake').value = s.stake || '';
  document.getElementById('edit-notes').value = s.notes || '';
  updateEditPreview();
  document.getElementById('edit-modal').style.display = 'flex';
}}

function updateEditPreview(){{
  const result = document.getElementById('edit-result').value;
  const odds = parseFloat(document.getElementById('edit-odds').value)||0;
  const stake = parseFloat(document.getElementById('edit-stake').value)||0;
  let pnl;
  if (result==='W' && odds>0) pnl = '+'+((odds-1)*stake).toFixed(2)+'€';
  else if (result==='L') pnl = (-stake).toFixed(2)+'€';
  else if (result==='VOID') pnl = '0.00€';
  else pnl = '— (open signal)';
  document.getElementById('edit-preview').textContent = pnl;
}}

['edit-result','edit-odds','edit-stake'].forEach(id=>{{
  document.addEventListener('DOMContentLoaded',()=>{{
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', updateEditPreview);
  }});
}});

function closeEditModal(){{
  document.getElementById('edit-modal').style.display = 'none';
  EDIT_CURRENT_IDX = null;
}}

async function saveEdit(){{
  if (EDIT_CURRENT_IDX === null) return;
  const payload = {{
    result: document.getElementById('edit-result').value,
    odds: document.getElementById('edit-odds').value || 0,
    stake: document.getElementById('edit-stake').value || 0,
    notes: document.getElementById('edit-notes').value || '',
  }};
  try {{
    const r = await fetch(`/api/signal/${{EDIT_CURRENT_IDX}}`, {{
      method: 'PATCH',
      headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify(payload),
    }});
    const data = await r.json();
    if (!data.ok){{
      alert('Error: ' + (data.error||'unknown'));
      return;
    }}
    closeEditModal();
    await reloadFromBackend();
  }} catch(e){{
    alert('Network error: ' + e.message);
  }}
}}

async function confirmDelete(idx){{
  if (!ADMIN_BACKEND_LIVE) return;
  const s = ADMIN_SIGNALS.find(x=>x.idx===idx);
  if (!s) return;
  if (!confirm(`Permanently delete signal #${{idx}} (${{s.player}} - ${{s.date}} ${{s.signal_time}})?\\n\\nRunning balance will be recalculated.`)) return;
  try {{
    const r = await fetch(`/api/signal/${{idx}}`, {{method: 'DELETE'}});
    const data = await r.json();
    if (!data.ok){{
      alert('Error: ' + (data.error||'unknown'));
      return;
    }}
    await reloadFromBackend();
  }} catch(e){{
    alert('Network error: ' + e.message);
  }}
}}

// === Init ===
renderKPIs();
renderMonthly();
renderDaily();
populateDays();
renderSignals();
detectBackend().then(()=>renderAdminTable());
</script>
</body>
</html>"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[Dashboard] Generated: {OUTPUT_HTML}")
    return OUTPUT_HTML


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    data = generate_data()
    path = generate_html(data)

    if not args.no_open:
        webbrowser.open(f"file:///{os.path.abspath(path)}")
