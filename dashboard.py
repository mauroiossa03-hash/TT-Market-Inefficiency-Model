"""
dashboard.py — Genera la dashboard HTML con:
  - PnL giornaliero e mensile
  - Classifiche gironi per giorno/orario
  - Analisi per posizione nel girone (1°, 2°, 3°, 4°, 5°, 6°)
  - Storico segnali completo

Uso:
  python dashboard.py             # genera e apre dashboard.html
  python dashboard.py --no-open   # genera senza aprire
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


BLOCK_SIGNAL_TIMES = {
    10: [(10, 0), (10, 30)],
    14: [(14, 0), (14, 30)],
    18: [(18, 0), (18, 30)],
    22: [(22, 0), (22, 30)],
}


def _classify_record(wins, losses):
    """Replica la logica di monitor.py: classifica un record in 1V1S / 2V / 2S / altro."""
    try:
        w, l = int(wins), int(losses)
    except (TypeError, ValueError):
        return "altro"
    if w == 1 and l == 1: return "1V1S"
    if w == 2 and l == 0: return "2V"
    if w == 0 and l == 2: return "2S"
    return "altro"


def _signal_block(signal_time):
    """Da '14:00' o '14:30' → 14. Restituisce None se non riconosciuto."""
    try:
        h = int(signal_time.split(":")[0])
    except Exception:
        return None
    for bh, times in BLOCK_SIGNAL_TIMES.items():
        for sh, _ in times:
            if h == sh:
                return bh
    return None


def _compute_position_for_signal(sig, db, standings_cache):
    """
    Calcola la posizione del giocatore puntato nella classifica del suo blocco
    storico (es. blocco 14 → match storici delle 12:00, 12:30, 13:00, 13:30).
    Usa una cache per evitare ricalcoli per (date, block).
    Restituisce un intero (1-6) o None se non determinabile.
    """
    d = sig.get("date", "")
    block_h = _signal_block(sig.get("signal_time", ""))
    if not d or not block_h:
        return None

    cache_key = (d, block_h)
    if cache_key not in standings_cache:
        day_events = db.get(d, {})
        hist_times = set(BLOCK_HISTORY.get(block_h, []))
        block_matches = [
            m for m in day_events.values()
            if m.get("status") == "finished"
            and not m.get("_baseline", False)
            and (m.get("hour"), m.get("minute")) in hist_times
        ]
        standings_cache[cache_key] = build_standings(block_matches) if block_matches else {}

    standings = standings_cache[cache_key]
    if not standings:
        return None

    player_name = sig.get("player", "")
    for gk, rows in standings.items():
        for i, r in enumerate(rows):
            if r["player_name"] == player_name:
                return i + 1
    return None


def _enrich_signals(signals, db):
    """
    Restituisce i segnali arricchiti con campi pre-calcolati per i filtri della tab Pivot:
      - _block: 10/14/18/22
      - _position: posizione girone (1-6) o None
      - _opp_class: 2V / 2S / altro
      - _odds_num, _stake_num, _pnl_num: versione numerica
    """
    standings_cache = {}
    enriched = []
    for s in signals:
        d = dict(s)  # shallow copy
        d["_block"] = _signal_block(s.get("signal_time", ""))
        d["_position"] = _compute_position_for_signal(s, db, standings_cache)
        d["_opp_class"] = _classify_record(s.get("opp_wins"), s.get("opp_losses"))
        try:
            d["_odds_num"] = float(s.get("odds")) if s.get("odds") else None
        except (TypeError, ValueError):
            d["_odds_num"] = None
        try:
            d["_stake_num"] = float(s.get("stake")) if s.get("stake") else 10.0
        except (TypeError, ValueError):
            d["_stake_num"] = 10.0
        try:
            d["_pnl_num"] = float(s.get("pnl")) if s.get("pnl") not in ("", None) else None
        except (TypeError, ValueError):
            d["_pnl_num"] = None
        enriched.append(d)
    return enriched


def generate_data():
    db = load_matches()
    signals = load_signals()

    # === PnL ===
    pnl_by_day = defaultdict(lambda: {"w":0,"l":0,"pnl":0.0,"bets":0,"signals":[]})
    pnl_monthly = defaultdict(lambda: {"w":0,"l":0,"pnl":0.0,"bets":0})

    for s in signals:
        d = s.get("date","")
        month = d[:7] if len(d) >= 7 else ""
        pnl_val = float(s.get("pnl") or 0)
        result = s.get("result","")
        odds = s.get("odds","")
        pnl_by_day[d]["bets"] += 1
        pnl_by_day[d]["signals"].append(s)
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

    # === Classifiche per giorno/blocco ===
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

    # === Analisi per posizione ===
    position_stats = defaultdict(lambda: {"w":0,"l":0,"total":0,"pnl":0.0})
    for s in signals:
        if s.get("result") not in ("W","L"): continue
        # Ricostruisci posizione
        d = s.get("date","")
        sig_time = s.get("signal_time","")
        try:
            sig_h = int(sig_time.split(":")[0])
        except: continue

        # Trova il blocco
        block_h = None
        for bh, stimes in {10:[(10,0),(10,30)],14:[(14,0),(14,30)],18:[(18,0),(18,30)],22:[(22,0),(22,30)]}.items():
            for sh, sm in stimes:
                if sig_h == sh:
                    block_h = bh
                    break
            if block_h: break
        if not block_h: continue

        # Ricostruisci classifica da DB
        day_events = db.get(d, {})
        hist_times = set(BLOCK_HISTORY.get(block_h, []))
        block_matches = [
            m for m in day_events.values()
            if m.get("status") == "finished"
            and not m.get("_baseline", False)
            and (m.get("hour"), m.get("minute")) in hist_times
        ]
        if not block_matches: continue
        standings = build_standings(block_matches)

        player_name = s.get("player","")
        pos = None
        for gk, rows in standings.items():
            for i, r in enumerate(rows):
                if r["player_name"] == player_name:
                    pos = i + 1
                    break
            if pos: break
        if not pos: continue

        pnl_val = float(s.get("pnl") or 0)
        position_stats[pos]["total"] += 1
        position_stats[pos]["pnl"] += pnl_val
        if s["result"] == "W":
            position_stats[pos]["w"] += 1
        else:
            position_stats[pos]["l"] += 1

    return {
        "pnl_by_day": dict(pnl_by_day),
        "pnl_monthly": dict(pnl_monthly),
        "standings": standings_data,
        "position_stats": dict(position_stats),
        "signals": signals,
        "signals_enriched": _enrich_signals(signals, db),
        "generated": datetime.now().isoformat(),
    }


def generate_html(data):
    signals_json = json.dumps(data["signals"], default=str)
    signals_enriched_json = json.dumps(data["signals_enriched"], default=str)
    pnl_day_json = json.dumps(data["pnl_by_day"], default=str)
    pnl_month_json = json.dumps(data["pnl_monthly"], default=str)
    standings_json = json.dumps(data["standings"], default=str)
    position_json = json.dumps(data["position_stats"], default=str)

    html = f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Czech Liga Pro — Dashboard</title>
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
.grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px}}
.grid2{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:24px}}
.kpi{{background:#18181b;border:1px solid #27272a;border-radius:10px;padding:16px}}
.kpi .lbl{{font-size:11px;color:#71717a;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}}
.kpi .val{{font-size:26px;font-weight:700}}
.kpi .val.g{{color:#4ade80}}.kpi .val.r{{color:#f87171}}.kpi .val.b{{color:#60a5fa}}.kpi .val.y{{color:#facc15}}
.kpi .sub{{font-size:11px;color:#52525b;margin-top:4px}}
.card{{background:#18181b;border:1px solid #27272a;border-radius:10px;padding:16px;margin-bottom:16px}}
.card h3{{font-size:14px;font-weight:600;color:#d4d4d8;margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.card h3 .dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:left;padding:8px 10px;color:#71717a;font-weight:500;border-bottom:1px solid #27272a;text-transform:uppercase;letter-spacing:.06em;font-size:10px}}
td{{padding:7px 10px;border-bottom:1px solid #1c1c1e;color:#d4d4d8}}
tr:hover td{{background:#1c1c1e}}
.win{{color:#4ade80;font-weight:600}}.loss{{color:#f87171;font-weight:600}}
.badge{{display:inline-block;font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px;letter-spacing:.04em}}
.badge.w{{background:#052e16;color:#4ade80}}.badge.l{{background:#450a0a;color:#f87171}}
.badge.open{{background:#1e1b4b;color:#818cf8}}
.bar-row{{display:flex;align-items:center;gap:10px;margin-bottom:8px}}
.bar-lbl{{font-size:12px;color:#71717a;width:70px;text-align:right}}
.bar-wrap{{flex:1;height:22px;background:#1c1c1e;border-radius:4px;overflow:hidden;border:1px solid #27272a}}
.bar-fill{{height:100%;border-radius:3px;transition:width .3s}}
.bar-val{{font-size:12px;font-weight:600;width:65px}}
.pos-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:12px}}
.pos-card{{background:#0f0f12;border:1px solid #27272a;border-radius:8px;padding:14px;text-align:center}}
.pos-card .pos{{font-size:28px;font-weight:700;color:#3f3f46;margin-bottom:4px}}
.pos-card .pos.active{{color:#4ade80}}
.pos-card .stat{{font-size:12px;color:#71717a;margin-top:4px}}
.pos-card .wr{{font-size:18px;font-weight:700;margin-top:2px}}
.filter-row{{display:flex;gap:8px;align-items:center;margin-bottom:16px;flex-wrap:wrap}}
.filter-row select,.filter-row input{{font-family:inherit;font-size:12px;background:#18181b;color:#d4d4d8;border:1px solid #27272a;border-radius:6px;padding:6px 10px}}
.filter-row label{{font-size:12px;color:#71717a}}
.refresh-btn{{background:#4ade80;color:#000;border:none;font-family:inherit;font-size:12px;font-weight:700;padding:8px 18px;border-radius:6px;cursor:pointer}}
.refresh-btn:hover{{background:#22c55e}}
.empty{{text-align:center;padding:40px;color:#3f3f46;font-size:13px}}
.chip{{display:inline-flex;align-items:center;gap:4px;font-size:12px;padding:4px 10px;border:1px solid #27272a;border-radius:14px;background:#0f0f12;color:#d4d4d8;cursor:pointer;user-select:none}}
.chip:has(input:checked){{border-color:#4ade80;color:#4ade80;background:#052e16}}
.chip input{{margin:0;cursor:pointer;accent-color:#4ade80}}
</style>
</head>
<body>
<div class="container">

<div class="hdr">
  <div>
    <h1>Czech Liga Pro — Dashboard</h1>
    <div class="sub">Strategia 1V1S · Quote bet365 · Analisi posizionale</div>
  </div>
  <div class="ts">Generata: {data['generated'][:16].replace('T',' ')}<br>
    <button class="refresh-btn" onclick="location.reload()" style="margin-top:6px">Aggiorna</button>
  </div>
</div>

<div class="tabs">
  <button class="tab on" onclick="sw('pnl',this)">P&L</button>
  <button class="tab" onclick="sw('standings',this)">Classifiche</button>
  <button class="tab" onclick="sw('positions',this)">Analisi posizione</button>
  <button class="tab" onclick="sw('pivot',this)">Pivot &amp; Stake</button>
  <button class="tab" onclick="sw('signals',this)">Storico segnali</button>
  <button class="tab" onclick="sw('admin',this)">DB Admin</button>
</div>

<!-- PNL TAB -->
<div id="s-pnl" class="sec on">
  <div class="grid4" id="kpi-row"></div>
  <div class="card"><h3>P&L mensile</h3><div id="pnl-monthly"></div></div>
  <div class="card"><h3>P&L giornaliero</h3><div id="pnl-daily"></div></div>
</div>

<!-- STANDINGS TAB -->
<div id="s-standings" class="sec">
  <div class="filter-row">
    <label>Giorno:</label>
    <select id="day-filter" onchange="onDayChange()"></select>
    <label>Blocco:</label>
    <select id="block-filter" onchange="renderStandings()"></select>
  </div>
  <div id="standings-container"></div>
</div>

<!-- POSITIONS TAB -->
<div id="s-positions" class="sec">
  <div class="card">
    <h3>Win rate per posizione nel girone</h3>
    <p style="font-size:12px;color:#71717a;margin-bottom:16px">
      Analizza se la posizione nel girone del giocatore puntato influenza il risultato.
      Un giocatore al 5°-6° posto ha piu' incentivo a vincere?
    </p>
    <div class="pos-grid" id="pos-grid"></div>
  </div>
  <div class="card">
    <h3>Dettaglio per posizione</h3>
    <div id="pos-detail"></div>
  </div>
</div>

<!-- PIVOT TAB -->
<div id="s-pivot" class="sec">

  <!-- FILTERS CARD -->
  <div class="card">
    <h3><span class="dot" style="background:#facc15"></span> Filtri</h3>

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-bottom:12px">

      <div>
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">FASCIA ORARIA</label>
        <div id="filter-blocks" style="display:flex;gap:6px;flex-wrap:wrap">
          <label class="chip"><input type="checkbox" value="10" checked onchange="recompute()"> 10:00</label>
          <label class="chip"><input type="checkbox" value="14" checked onchange="recompute()"> 14:00</label>
          <label class="chip"><input type="checkbox" value="18" checked onchange="recompute()"> 18:00</label>
          <label class="chip"><input type="checkbox" value="22" checked onchange="recompute()"> 22:00</label>
        </div>
      </div>

      <div>
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">LATO BET</label>
        <div id="filter-side" style="display:flex;gap:6px;flex-wrap:wrap">
          <label class="chip"><input type="checkbox" value="home" checked onchange="recompute()"> Home</label>
          <label class="chip"><input type="checkbox" value="away" checked onchange="recompute()"> Away</label>
        </div>
      </div>

      <div>
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">CLASSE AVVERSARIO</label>
        <div id="filter-oppclass" style="display:flex;gap:6px;flex-wrap:wrap">
          <label class="chip"><input type="checkbox" value="2V" checked onchange="recompute()"> Avv. 2V</label>
          <label class="chip"><input type="checkbox" value="2S" checked onchange="recompute()"> Avv. 2S</label>
        </div>
      </div>

      <div>
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">RANGE QUOTA</label>
        <div style="display:flex;gap:6px;align-items:center">
          <input type="number" id="odds-min" step="0.01" min="1" placeholder="min" value="1.60" onchange="recompute()" style="width:80px">
          <span style="color:#52525b">—</span>
          <input type="number" id="odds-max" step="0.01" min="1" placeholder="max" value="4.00" onchange="recompute()" style="width:80px">
        </div>
      </div>

      <div>
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">POSIZIONE GIRONE</label>
        <div id="filter-position" style="display:flex;gap:4px;flex-wrap:wrap">
          <label class="chip"><input type="checkbox" value="1" checked onchange="recompute()"> 1°</label>
          <label class="chip"><input type="checkbox" value="2" checked onchange="recompute()"> 2°</label>
          <label class="chip"><input type="checkbox" value="3" checked onchange="recompute()"> 3°</label>
          <label class="chip"><input type="checkbox" value="4" checked onchange="recompute()"> 4°</label>
          <label class="chip"><input type="checkbox" value="5" checked onchange="recompute()"> 5°</label>
          <label class="chip"><input type="checkbox" value="6" checked onchange="recompute()"> 6°</label>
          <label class="chip" style="border-color:#52525b"><input type="checkbox" id="pos-unknown" checked onchange="recompute()"> N/A</label>
        </div>
      </div>

    </div>

    <div style="font-size:11px;color:#52525b;border-top:1px solid #27272a;padding-top:10px">
      <strong style="color:#71717a">N/A</strong>: segnali per cui non è stato possibile calcolare la posizione (history del blocco mancante o giocatore non trovato).
    </div>
  </div>

  <!-- STAKE MODEL CARD -->
  <div class="card">
    <h3><span class="dot" style="background:#60a5fa"></span> Modello di stake</h3>

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px">

      <div>
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">MODELLO</label>
        <select id="stake-model" onchange="onStakeModelChange();recompute()">
          <option value="flat">Flat (stake fisso)</option>
          <option value="loss_progression">Progression dopo N perdite consecutive</option>
          <option value="red_day_progression">Progression dopo N giorni rossi consecutivi</option>
        </select>
      </div>

      <div>
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">STAKE BASE €</label>
        <input type="number" id="stake-base" step="1" min="1" value="10" onchange="recompute()" style="width:100%">
      </div>

      <div id="param-trigger" style="display:none">
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px"><span id="param-trigger-lbl">N TRIGGER</span></label>
        <input type="number" id="stake-trigger" step="1" min="1" value="4" onchange="recompute()" style="width:100%">
      </div>

      <div id="param-delta" style="display:none">
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">INCREMENTO Δ (% del base)</label>
        <select id="stake-delta" onchange="recompute()">
          <option value="0.10" selected>+10%</option>
          <option value="0.20">+20%</option>
          <option value="0.50">+50%</option>
          <option value="1.00">+100% (raddoppio)</option>
        </select>
      </div>

      <div id="param-cap" style="display:none">
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">CAP MAX (× base)</label>
        <select id="stake-cap" onchange="recompute()">
          <option value="2">2×</option>
          <option value="3">3×</option>
          <option value="4" selected>4×</option>
          <option value="6">6×</option>
          <option value="100">∞ (no cap)</option>
        </select>
      </div>

      <div id="param-reset" style="display:none">
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">RESET</label>
        <select id="stake-reset" onchange="recompute()">
          <option value="first_win" selected>Alla prima vittoria / giorno verde</option>
          <option value="never">Mai (cumulativo)</option>
        </select>
      </div>
    </div>

    <div style="font-size:11px;color:#52525b;border-top:1px solid #27272a;padding-top:10px;margin-top:12px">
      <strong style="color:#facc15">⚠ Avvertenza</strong>: i modelli Progression sono varianti di martingala. Non aumentano l'aspettativa attesa di un sistema con edge positivo, ma aumentano la varianza e il rischio di drawdown estremo. Confronta sempre con Flat sui numeri reali del dataset.
    </div>
  </div>

  <!-- RESULTS CARD -->
  <div class="grid4" id="pivot-kpi"></div>

  <div class="card">
    <h3><span class="dot" style="background:#4ade80"></span> Risultato del filtro + modello</h3>
    <div id="pivot-summary" style="font-size:13px;color:#a1a1aa;line-height:1.8"></div>
  </div>

  <div class="grid2">
    <div class="card">
      <h3>Breakdown per fascia oraria</h3>
      <div id="pivot-by-block"></div>
    </div>
    <div class="card">
      <h3>Breakdown per posizione girone</h3>
      <div id="pivot-by-pos"></div>
    </div>
  </div>

  <div class="card">
    <h3>Equity curve (PnL cumulativo nel tempo)</h3>
    <div id="pivot-equity" style="height:200px;position:relative"></div>
  </div>

  <div class="card">
    <h3>Segnali nel filtro <span id="pivot-count" style="font-size:11px;color:#71717a;font-weight:400;margin-left:8px"></span></h3>
    <div style="overflow-x:auto;max-height:400px;overflow-y:auto"><table>
      <thead><tr>
        <th>Data</th><th>Ora</th><th>Blk</th><th>Giocatore</th><th>Avv. cls</th><th>Lato</th>
        <th>Pos</th><th>Quota</th><th>Stake€</th><th>PnL</th>
      </tr></thead>
      <tbody id="pivot-body"></tbody>
    </table></div>
  </div>

</div>

<!-- SIGNALS TAB -->
<div id="s-signals" class="sec">
  <div class="card">
    <h3>Storico completo segnali</h3>
    <div style="overflow-x:auto"><table id="sig-table">
      <thead><tr>
        <th>Data</th><th>Ora</th><th>Giocatore</th><th>Avversario</th>
        <th>Record</th><th>Avv. record</th><th>Quota</th><th>Stake</th>
        <th>Esito</th><th>P&L</th><th>Balance</th>
      </tr></thead>
      <tbody id="sig-body"></tbody>
    </table></div>
  </div>
</div>

<!-- ADMIN TAB -->
<div id="s-admin" class="sec">
  <div class="card" id="admin-banner-static" style="display:none;border-color:#facc15;background:#1c1a07">
    <h3 style="color:#facc15">⚠ Modalità file statica</h3>
    <p style="font-size:13px;color:#d4d4d8;line-height:1.6">
      Stai visualizzando questa dashboard come file HTML aperto direttamente dal browser.
      I bottoni di edit/delete sono <strong>disattivati</strong> in questa modalità.<br><br>
      Per abilitarli, lancia il server Flask:
    </p>
    <pre style="background:#09090b;border:1px solid #27272a;padding:10px;border-radius:6px;font-size:12px;color:#4ade80;margin-top:8px">cd "C:\\path\\to\\QuantBetv3"
python dashboard_server.py
# poi apri http://localhost:5000</pre>
  </div>

  <div class="card" id="admin-banner-live" style="display:none;border-color:#4ade80;background:#062a14">
    <h3 style="color:#4ade80">● Modalità live (Flask)</h3>
    <p style="font-size:12px;color:#a1a1aa">
      Edit e delete sono attivi. Le modifiche aggiornano il CSV e ricalcolano automaticamente
      PnL e balance progressivo. <strong style="color:#facc15">Importante</strong>: per evitare race
      condition con il bot, fai modifiche FUORI dalle finestre di monitoraggio (10:30–11:55, 14:30–15:55, ecc.)
      oppure ferma temporaneamente il bot.
    </p>
  </div>

  <div class="card">
    <h3>Gestione segnali</h3>
    <div class="filter-row" style="margin-bottom:12px">
      <label>Cerca giocatore:</label>
      <input type="text" id="admin-search" placeholder="es. Schwan" oninput="renderAdminTable()" style="min-width:180px">
      <label>Filtra esito:</label>
      <select id="admin-filter-result" onchange="renderAdminTable()">
        <option value="">Tutti</option>
        <option value="open">Solo aperti</option>
        <option value="W">Solo W</option>
        <option value="L">Solo L</option>
        <option value="VOID">Solo VOID</option>
      </select>
      <button class="refresh-btn" onclick="reloadFromBackend()">🔄 Ricarica da DB</button>
    </div>

    <div style="overflow-x:auto;max-height:600px;overflow-y:auto">
      <table id="admin-table">
        <thead><tr>
          <th>#</th><th>Data</th><th>Ora</th><th>Giocatore</th><th>Avv.</th>
          <th>Quota</th><th>Stake</th><th>Esito</th><th>P&L</th><th>Bal</th><th>Note</th><th>Azioni</th>
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
    <h3 style="font-size:15px;color:#fafafa;margin-bottom:16px">Modifica segnale <span id="edit-title" style="color:#71717a;font-weight:400"></span></h3>

    <div style="display:grid;gap:12px;font-size:13px">
      <div>
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">RISULTATO</label>
        <select id="edit-result" style="width:100%">
          <option value="">— aperto —</option>
          <option value="W">W (vinto)</option>
          <option value="L">L (perso)</option>
          <option value="VOID">VOID (annullato)</option>
        </select>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div>
          <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">QUOTA</label>
          <input type="number" id="edit-odds" step="0.01" min="1" style="width:100%">
        </div>
        <div>
          <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">STAKE €</label>
          <input type="number" id="edit-stake" step="0.5" min="0" style="width:100%">
        </div>
      </div>
      <div>
        <label style="font-size:11px;color:#71717a;display:block;margin-bottom:4px">NOTE</label>
        <input type="text" id="edit-notes" style="width:100%">
      </div>
      <div style="font-size:11px;color:#52525b;background:#0f0f12;padding:8px;border-radius:6px;border:1px solid #27272a">
        Anteprima PnL: <strong id="edit-preview" style="color:#fafafa">—</strong>
      </div>
    </div>

    <div style="display:flex;gap:8px;margin-top:20px;justify-content:flex-end">
      <button onclick="closeEditModal()" style="background:transparent;border:1px solid #27272a;color:#a1a1aa;padding:8px 16px;border-radius:6px;font-family:inherit;cursor:pointer">Annulla</button>
      <button onclick="saveEdit()" class="refresh-btn">💾 Salva</button>
    </div>
  </div>
</div>

</div>

<script>
const SIGNALS = {signals_json};
const SIGNALS_ENRICHED = {signals_enriched_json};
const PNL_DAY = {pnl_day_json};
const PNL_MONTH = {pnl_month_json};
const STANDINGS = {standings_json};
const POS_STATS = {position_json};

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
    <div class="kpi"><div class="lbl">P&L totale</div><div class="val ${{totalPnl>=0?'g':'r'}}">${{totalPnl>=0?'+':''}}${{totalPnl.toFixed(2)}}€</div><div class="sub">${{settled.length}} scommesse chiuse</div></div>
    <div class="kpi"><div class="lbl">Win rate</div><div class="val ${{wr>=54?'g':'r'}}">${{wr}}%</div><div class="sub">${{wins}}V / ${{losses}}P</div></div>
    <div class="kpi"><div class="lbl">P&L oggi</div><div class="val ${{todayPnl>=0?'g':'r'}}">${{todayPnl>=0?'+':''}}${{todayPnl.toFixed(2)}}€</div><div class="sub">${{todayData.bets||0}} scommesse</div></div>
    <div class="kpi"><div class="lbl">Segnali aperti</div><div class="val b">${{openCount}}</div><div class="sub">${{SIGNALS.length}} totali</div></div>
  `;
}}

// === PNL Monthly ===
function renderMonthly() {{
  const months = Object.entries(PNL_MONTH).sort((a,b)=>a[0].localeCompare(b[0]));
  if (!months.length) {{ document.getElementById('pnl-monthly').innerHTML='<div class="empty">Nessun dato</div>'; return; }}
  const maxAbs = Math.max(...months.map(([,v])=>Math.abs(parseFloat(v.pnl||0))), 1);
  document.getElementById('pnl-monthly').innerHTML = months.map(([m,v]) => {{
    const pnl = parseFloat(v.pnl||0);
    const pct = Math.round(Math.abs(pnl)/maxAbs*100);
    const col = pnl>=0 ? '#4ade80':'#f87171';
    const bg = pnl>=0 ? '#052e16':'#450a0a';
    const wr = v.w+v.l > 0 ? Math.round(v.w/(v.w+v.l)*100) : 0;
    return `<div class="bar-row"><span class="bar-lbl">${{m}}</span><div class="bar-wrap"><div class="bar-fill" style="width:${{pct}}%;background:${{bg}};border-right:3px solid ${{col}}"></div></div><span class="bar-val" style="color:${{col}}">${{pnl>=0?'+':''}}${{pnl.toFixed(2)}}€</span><span style="font-size:11px;color:#52525b">${{v.w}}V ${{v.l}}P (${{wr}}%)</span></div>`;
  }}).join('');
}}

// === PNL Daily ===
function renderDaily() {{
  const days = Object.entries(PNL_DAY).sort((a,b)=>b[0].localeCompare(a[0]));
  if (!days.length) {{ document.getElementById('pnl-daily').innerHTML='<div class="empty">Nessun dato</div>'; return; }}
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
    return `<div class="bar-row"><span class="bar-lbl">${{d.slice(5)}}</span><div class="bar-wrap"><div class="bar-fill" style="width:${{pct}}%;background:${{bg}};border-right:3px solid ${{col}}"></div></div><span class="bar-val" style="color:${{col}}">${{pnl>=0?'+':''}}${{pnl.toFixed(2)}}€</span><span style="font-size:11px;color:#52525b">${{v.w}}V${{v.l}}P (${{wr}}%)</span><span style="font-size:11px;color:${{cum>=0?'#4ade80':'#f87171'}};margin-left:8px">cum: ${{cum>=0?'+':''}}${{cum.toFixed(2)}}</span></div>`;
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
  // Quando cambia il giorno, ripopola i blocchi e renderizza
  const day = document.getElementById('day-filter').value;
  const dayData = STANDINGS[day] || {{}};
  const blocks = Object.keys(dayData).sort();
  const blockFilter = document.getElementById('block-filter');
  blockFilter.innerHTML = '<option value="all">Tutti</option>' +
    blocks.map(b => `<option value="${{b}}">${{b}}</option>`).join('');
  renderStandings();
}}

function renderStandings() {{
  const day = document.getElementById('day-filter').value;
  const selectedBlock = document.getElementById('block-filter').value;
  const dayData = STANDINGS[day] || {{}};
  const blocks = Object.keys(dayData).sort();

  const container = document.getElementById('standings-container');
  if (!blocks.length) {{ container.innerHTML='<div class="empty">Nessun dato per questo giorno</div>'; return; }}

  const showBlocks = selectedBlock === 'all' ? blocks : [selectedBlock];

  container.innerHTML = showBlocks.filter(b=>dayData[b]).map(block => {{
    const groups = dayData[block];
    return Object.entries(groups).map(([gk, rows]) => {{
      const tbody = rows.map((r,i) => {{
        const diff = r.sets_won - r.sets_lost;
        const cls = r.wins===1&&r.losses===1 ? 'style="color:#4ade80"' :
                    r.wins===2&&r.losses===0 ? 'style="color:#60a5fa"' :
                    r.wins===0&&r.losses===2 ? 'style="color:#f87171"' : '';
        return `<tr><td>${{i+1}}</td><td ${{cls}}>${{r.player_name}}</td><td>${{r.wins}}</td><td>${{r.losses}}</td><td>${{r.sets_won}}</td><td>${{r.sets_lost}}</td><td>${{diff>=0?'+':''}}${{diff}}</td></tr>`;
      }}).join('');
      return `<div class="card"><h3><span class="dot" style="background:#4ade80"></span> Blocco ${{block}} — Girone ${{gk}}</h3>
        <table><thead><tr><th>#</th><th>Giocatore</th><th>V</th><th>S</th><th>Set+</th><th>Set-</th><th>Diff</th></tr></thead>
        <tbody>${{tbody}}</tbody></table></div>`;
    }}).join('');
  }}).join('');
}}

// === Position Analysis ===
function renderPositions() {{
  const grid = document.getElementById('pos-grid');
  const detail = document.getElementById('pos-detail');
  const positions = [1,2,3,4,5,6];

  grid.innerHTML = positions.map(p => {{
    const s = POS_STATS[p] || {{w:0,l:0,total:0,pnl:0}};
    const wr = s.total > 0 ? Math.round(s.w/s.total*100) : 0;
    const pnl = parseFloat(s.pnl||0);
    const active = s.total > 0;
    return `<div class="pos-card">
      <div class="pos ${{active?'active':''}}">#${{p}}</div>
      <div class="wr" style="color:${{wr>=54?'#4ade80':wr>0?'#f87171':'#3f3f46'}}">${{s.total?wr+'%':'—'}}</div>
      <div class="stat">${{s.w}}V / ${{s.l}}P</div>
      <div class="stat" style="color:${{pnl>=0?'#4ade80':'#f87171'}}">${{s.total?(pnl>=0?'+':'')+pnl.toFixed(2)+'€':'—'}}</div>
    </div>`;
  }}).join('');

  // Insight
  const active = positions.filter(p => (POS_STATS[p]||{{}}).total > 0);
  if (!active.length) {{
    detail.innerHTML = '<div class="empty">Serve piu\\' dati per l\\'analisi posizionale. Continua a raccogliere segnali.</div>';
    return;
  }}
  const best = active.reduce((a,b) => {{
    const wa = (POS_STATS[a]||{{}}).total ? (POS_STATS[a]||{{}}).w / (POS_STATS[a]||{{}}).total : 0;
    const wb = (POS_STATS[b]||{{}}).total ? (POS_STATS[b]||{{}}).w / (POS_STATS[b]||{{}}).total : 0;
    return wa >= wb ? a : b;
  }});
  const bestWr = Math.round(((POS_STATS[best]||{{}}).w / (POS_STATS[best]||{{}}).total)*100);
  detail.innerHTML = `<div style="padding:12px;background:#0f0f12;border:1px solid #27272a;border-radius:8px;font-size:13px;color:#a1a1aa;line-height:1.8">
    Posizione con miglior win rate: <strong style="color:#4ade80">#${{best}} (${{bestWr}}%)</strong><br>
    Strategia attuale (1V1S puro) punta indipendentemente dalla posizione.<br>
    Se la posizione #5 o #6 mostra un win rate superiore, potrebbe valere la pena filtrarci sopra.
    <br><br><strong style="color:#71717a">Nota:</strong> servono almeno 30-50 segnali per avere significativita' statistica.
  </div>`;
}}

// === Signals Table ===
function renderSignals() {{
  const body = document.getElementById('sig-body');
  if (!SIGNALS.length) {{ body.innerHTML='<tr><td colspan="11" style="text-align:center;color:#3f3f46;padding:40px">Nessun segnale registrato</td></tr>'; return; }}
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
      <td>${{s.player_wins||0}}V-${{s.player_losses||0}}S</td>
      <td>${{s.opp_wins||0}}V-${{s.opp_losses||0}}S</td>
      <td>${{s.odds||'—'}}</td><td>€${{s.stake||10}}</td>
      <td>${{badge}}</td>
      <td style="color:${{pnlCol}};font-weight:600">${{pnlStr}}</td>
      <td style="color:#71717a">${{bal}}</td>
    </tr>`;
  }}).join('');
}}

// === PIVOT TAB ===
function getCheckedValues(containerId){{
  return Array.from(document.querySelectorAll('#'+containerId+' input[type=checkbox]:checked')).map(c=>c.value);
}}

function onStakeModelChange(){{
  const m = document.getElementById('stake-model').value;
  const trigger = document.getElementById('param-trigger');
  const triggerLbl = document.getElementById('param-trigger-lbl');
  const delta = document.getElementById('param-delta');
  const cap = document.getElementById('param-cap');
  const reset = document.getElementById('param-reset');
  if (m === 'flat'){{
    trigger.style.display='none'; delta.style.display='none'; cap.style.display='none'; reset.style.display='none';
  }} else {{
    trigger.style.display='block'; delta.style.display='block'; cap.style.display='block'; reset.style.display='block';
    if (m === 'loss_progression'){{
      triggerLbl.textContent='N PERDITE CONSECUTIVE';
      document.getElementById('stake-trigger').value = document.getElementById('stake-trigger').value || 4;
    }} else {{
      triggerLbl.textContent='N GIORNI ROSSI CONSECUTIVI';
      document.getElementById('stake-trigger').value = 2;
    }}
  }}
}}

// --- STAKE MODELS in JS (replica di stake_models.py per ricalcolo on-the-fly) ---
function sortByDateTime(arr){{
  return [...arr].sort((a,b)=>{{
    const da=(a.date||'')+' '+(a.signal_time||'00:00');
    const db=(b.date||'')+' '+(b.signal_time||'00:00');
    return da.localeCompare(db);
  }});
}}

function applyStakeFlat(signals, base){{
  return signals.map(s=>({{...s, _applied_stake: base}}));
}}

function applyStakeLossProgression(signals, base, nTrigger, delta, capMult, reset){{
  const sorted = sortByDateTime(signals);
  let streak=0, steps=0;
  return sorted.map(s=>{{
    const mult = Math.min(1.0 + delta*steps, capMult);
    const applied = +(base*mult).toFixed(2);
    const out = {{...s, _applied_stake: applied}};
    if (s.result==='L'){{
      streak++;
      if (streak >= nTrigger){{ steps++; streak=0; }}
    }} else if (s.result==='W'){{
      streak=0;
      if (reset==='first_win') steps=0;
    }}
    return out;
  }});
}}

function applyStakeRedDayProgression(signals, base, nTrigger, delta, capMult, reset){{
  const sorted = sortByDateTime(signals);
  // PnL giornaliero a stake BASE
  const dailyPnl = {{}};
  sorted.forEach(s=>{{
    const d = s.date||''; if (!d) return;
    const odds = parseFloat(s.odds||0);
    if (s.result==='W') dailyPnl[d] = (dailyPnl[d]||0) + (odds-1)*base;
    else if (s.result==='L') dailyPnl[d] = (dailyPnl[d]||0) - base;
  }});
  const sortedDays = Object.keys(dailyPnl).sort();
  const dayMult = {{}};
  let consecRed=0, steps=0;
  sortedDays.forEach(d=>{{
    dayMult[d] = Math.min(1.0 + delta*steps, capMult);
    if (dailyPnl[d] < 0){{
      consecRed++;
      if (consecRed >= nTrigger){{ steps++; consecRed=0; }}
    }} else if (dailyPnl[d] > 0){{
      consecRed=0;
      if (reset==='first_win'||reset==='green_day') steps=0;
    }}
  }});
  const lastMult = sortedDays.length ? dayMult[sortedDays[sortedDays.length-1]] : 1.0;
  return sorted.map(s=>{{
    const m = dayMult[s.date] !== undefined ? dayMult[s.date] : lastMult;
    return {{...s, _applied_stake: +(base*m).toFixed(2)}};
  }});
}}

function applyAppliedPnl(signals){{
  return signals.map(s=>{{
    const odds = parseFloat(s.odds||0);
    const stk = parseFloat(s._applied_stake||0);
    let pnl = 0;
    if (s.result==='W' && odds>0) pnl = +((odds-1)*stk).toFixed(2);
    else if (s.result==='L') pnl = -stk;
    return {{...s, _applied_pnl: pnl}};
  }});
}}

// --- FILTRO ---
function applyFilters(signals){{
  const blocks = getCheckedValues('filter-blocks').map(Number);
  const sides = getCheckedValues('filter-side');
  const oppCls = getCheckedValues('filter-oppclass');
  const positions = getCheckedValues('filter-position').map(Number);
  const includeNA = document.getElementById('pos-unknown').checked;
  const oddsMin = parseFloat(document.getElementById('odds-min').value) || 0;
  const oddsMax = parseFloat(document.getElementById('odds-max').value) || 999;

  return signals.filter(s=>{{
    if (s._block && !blocks.includes(s._block)) return false;
    if (s.player_side && !sides.includes(s.player_side)) return false;
    if (s._opp_class && !['2V','2S'].includes(s._opp_class)) return false; // escludi 'altro' sempre? — sì, non sono segnali 1V1S vs target
    if (!oppCls.includes(s._opp_class)) return false;
    if (s._odds_num !== null){{
      if (s._odds_num < oddsMin || s._odds_num > oddsMax) return false;
    }}
    if (s._position === null || s._position === undefined){{
      if (!includeNA) return false;
    }} else {{
      if (!positions.includes(s._position)) return false;
    }}
    return true;
  }});
}}

// --- METRICHE ---
function computeMetrics(signals){{
  const closed = signals.filter(s=>s.result==='W'||s.result==='L');
  if (!closed.length) return {{n:0,wins:0,losses:0,wr:0,pnl:0,roi:0,maxDD:0,maxStreakL:0}};
  let totalPnl=0, totalStake=0, wins=0, losses=0;
  closed.forEach(s=>{{
    totalPnl += parseFloat(s._applied_pnl||0);
    totalStake += parseFloat(s._applied_stake||0);
    if (s.result==='W') wins++; else losses++;
  }});
  const sorted = sortByDateTime(closed);
  let running=0, peak=0, maxDD=0, curStreak=0, maxStreak=0;
  sorted.forEach(s=>{{
    running += parseFloat(s._applied_pnl||0);
    if (running > peak) peak = running;
    const dd = peak - running;
    if (dd > maxDD) maxDD = dd;
    if (s.result==='L'){{ curStreak++; if(curStreak>maxStreak) maxStreak=curStreak; }}
    else curStreak=0;
  }});
  return {{
    n: closed.length, wins, losses,
    wr: closed.length ? +(wins/closed.length*100).toFixed(2) : 0,
    pnl: +totalPnl.toFixed(2),
    roi: totalStake ? +(totalPnl/totalStake*100).toFixed(2) : 0,
    maxDD: +maxDD.toFixed(2),
    maxStreakL: maxStreak,
  }};
}}

// --- BREAKDOWN BY DIM ---
function breakdownBy(signals, keyFn){{
  const groups = {{}};
  signals.forEach(s=>{{
    const k = keyFn(s);
    if (k===null||k===undefined) return;
    if (!groups[k]) groups[k] = [];
    groups[k].push(s);
  }});
  return Object.entries(groups).map(([k,arr])=>({{key:k, ...computeMetrics(arr)}}));
}}

// --- RENDER ---
function renderPivot(){{
  // 1. applico stake model
  const model = document.getElementById('stake-model').value;
  const base = parseFloat(document.getElementById('stake-base').value)||10;
  const trigger = parseInt(document.getElementById('stake-trigger').value)||4;
  const delta = parseFloat(document.getElementById('stake-delta').value)||0.10;
  const cap = parseFloat(document.getElementById('stake-cap').value)||4;
  const reset = document.getElementById('stake-reset').value||'first_win';

  let signals;
  if (model==='flat') signals = applyStakeFlat(SIGNALS_ENRICHED, base);
  else if (model==='loss_progression') signals = applyStakeLossProgression(SIGNALS_ENRICHED, base, trigger, delta, cap, reset);
  else signals = applyStakeRedDayProgression(SIGNALS_ENRICHED, base, trigger, delta, cap, reset);

  signals = applyAppliedPnl(signals);

  // 2. filtri
  const filtered = applyFilters(signals);

  // 3. KPI
  const m = computeMetrics(filtered);
  // confronto flat (con stessi filtri ma stake fisso)
  const flatBaseline = applyAppliedPnl(applyStakeFlat(SIGNALS_ENRICHED, base));
  const flatFiltered = applyFilters(flatBaseline);
  const mFlat = computeMetrics(flatFiltered);
  const delta_pnl = +(m.pnl - mFlat.pnl).toFixed(2);

  document.getElementById('pivot-kpi').innerHTML = `
    <div class="kpi"><div class="lbl">P&L (modello)</div><div class="val ${{m.pnl>=0?'g':'r'}}">${{m.pnl>=0?'+':''}}${{m.pnl.toFixed(2)}}€</div><div class="sub">${{m.n}} chiuse · ROI ${{m.roi}}%</div></div>
    <div class="kpi"><div class="lbl">Win rate</div><div class="val ${{m.wr>=54?'g':'r'}}">${{m.wr}}%</div><div class="sub">${{m.wins}}V / ${{m.losses}}P</div></div>
    <div class="kpi"><div class="lbl">Max drawdown</div><div class="val r">-${{m.maxDD.toFixed(2)}}€</div><div class="sub">streak max ${{m.maxStreakL}}P</div></div>
    <div class="kpi"><div class="lbl">vs Flat</div><div class="val ${{delta_pnl>=0?'g':'r'}}">${{delta_pnl>=0?'+':''}}${{delta_pnl.toFixed(2)}}€</div><div class="sub">flat: ${{mFlat.pnl>=0?'+':''}}${{mFlat.pnl.toFixed(2)}}€</div></div>
  `;

  // 4. Summary testuale
  const total = SIGNALS_ENRICHED.length;
  const sigSize = filtered.length;
  let warning = '';
  if (sigSize < 30){{
    warning = `<span style="color:#facc15">⚠ Sample size ${{sigSize}} < 30 — significatività statistica bassa.</span><br>`;
  }}
  document.getElementById('pivot-summary').innerHTML = `
    Filtrati <strong style="color:#fafafa">${{sigSize}}</strong> segnali su ${{total}} totali.<br>
    ${{warning}}
    Modello stake: <strong>${{model}}</strong> · base €${{base}}.
    ${{model!=='flat' ? ' Trigger=' + trigger + ' · Δ=' + (delta*100) + '% · Cap=' + cap + '× · Reset=' + reset + '.' : ''}}
  `;

  // 5. Breakdown
  const byBlock = breakdownBy(filtered, s=>s._block).sort((a,b)=>parseInt(a.key)-parseInt(b.key));
  document.getElementById('pivot-by-block').innerHTML = byBlock.length
    ? `<table><thead><tr><th>Blocco</th><th>N</th><th>WR</th><th>P&L</th><th>ROI</th></tr></thead><tbody>${{
        byBlock.map(b=>`<tr><td>${{b.key}}:00</td><td>${{b.n}}</td><td>${{b.wr}}%</td><td style="color:${{b.pnl>=0?'#4ade80':'#f87171'}}">${{b.pnl>=0?'+':''}}${{b.pnl.toFixed(2)}}€</td><td>${{b.roi}}%</td></tr>`).join('')
      }}</tbody></table>`
    : '<div class="empty">Nessun dato</div>';

  const byPos = breakdownBy(filtered, s=>s._position||'N/A').sort((a,b)=>{{
    if (a.key==='N/A') return 1; if (b.key==='N/A') return -1; return parseInt(a.key)-parseInt(b.key);
  }});
  document.getElementById('pivot-by-pos').innerHTML = byPos.length
    ? `<table><thead><tr><th>Pos</th><th>N</th><th>WR</th><th>P&L</th><th>ROI</th></tr></thead><tbody>${{
        byPos.map(b=>`<tr><td>${{b.key==='N/A'?'N/A':'#'+b.key}}</td><td>${{b.n}}</td><td>${{b.wr}}%</td><td style="color:${{b.pnl>=0?'#4ade80':'#f87171'}}">${{b.pnl>=0?'+':''}}${{b.pnl.toFixed(2)}}€</td><td>${{b.roi}}%</td></tr>`).join('')
      }}</tbody></table>`
    : '<div class="empty">Nessun dato</div>';

  // 6. Equity curve (SVG inline)
  const closedSorted = sortByDateTime(filtered.filter(s=>s.result==='W'||s.result==='L'));
  const eqEl = document.getElementById('pivot-equity');
  if (!closedSorted.length){{
    eqEl.innerHTML = '<div class="empty">Nessun dato per equity curve</div>';
  }} else {{
    let run=0;
    const points = closedSorted.map((s,i)=>{{ run += parseFloat(s._applied_pnl||0); return {{i, run}}; }});
    const minV = Math.min(0, ...points.map(p=>p.run));
    const maxV = Math.max(0, ...points.map(p=>p.run));
    const range = (maxV-minV)||1;
    const W=800, H=180, pad=20;
    const xs = i => pad + (i/(points.length-1||1))*(W-pad*2);
    const ys = v => H-pad - ((v-minV)/range)*(H-pad*2);
    const path = points.map((p,i)=>`${{i===0?'M':'L'}}${{xs(p.i).toFixed(1)}},${{ys(p.run).toFixed(1)}}`).join(' ');
    const zeroY = ys(0);
    const finalCol = run>=0 ? '#4ade80' : '#f87171';
    eqEl.innerHTML = `<svg viewBox="0 0 ${{W}} ${{H}}" style="width:100%;height:100%">
      <line x1="${{pad}}" y1="${{zeroY}}" x2="${{W-pad}}" y2="${{zeroY}}" stroke="#3f3f46" stroke-dasharray="2,3"/>
      <path d="${{path}}" fill="none" stroke="${{finalCol}}" stroke-width="2"/>
      <text x="${{pad}}" y="14" fill="#71717a" font-size="11">PnL cumulativo · finale: ${{run>=0?'+':''}}${{run.toFixed(2)}}€</text>
      <text x="${{W-pad}}" y="14" fill="#71717a" font-size="11" text-anchor="end">${{points.length}} bet</text>
    </svg>`;
  }}

  // 7. Tabella segnali filtrati
  document.getElementById('pivot-count').textContent = `(${{filtered.length}})`;
  const sortedFiltered = sortByDateTime(filtered).reverse();
  document.getElementById('pivot-body').innerHTML = sortedFiltered.length
    ? sortedFiltered.map(s=>{{
        const pnl = parseFloat(s._applied_pnl||0);
        const pnlCol = pnl>=0?'#4ade80':'#f87171';
        const resBadge = s.result==='W' ? '<span style="color:#4ade80">W</span>' : s.result==='L' ? '<span style="color:#f87171">L</span>' : '<span style="color:#818cf8">OPEN</span>';
        return `<tr>
          <td>${{s.date||''}}</td><td>${{s.signal_time||''}}</td><td>${{s._block||''}}:00</td>
          <td style="font-weight:600">${{s.player||''}}</td>
          <td>${{s._opp_class||''}}</td>
          <td>${{s.player_side||''}}</td>
          <td>${{s._position?'#'+s._position:'N/A'}}</td>
          <td>${{s._odds_num||'—'}}</td>
          <td>${{(s._applied_stake||0).toFixed(2)}}</td>
          <td style="color:${{pnlCol}};font-weight:600">${{s.result?(pnl>=0?'+':'')+pnl.toFixed(2):resBadge}}</td>
        </tr>`;
      }}).join('')
    : '<tr><td colspan="10" style="text-align:center;color:#3f3f46;padding:30px">Nessun segnale corrisponde ai filtri</td></tr>';
}}

function recompute(){{ renderPivot(); }}

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
    alert('Backend non disponibile. Lancia "python dashboard_server.py" e ricarica la pagina.');
    return;
  }}
  try {{
    const r = await fetch('/api/signals', {{cache:'no-store'}});
    const data = await r.json();
    ADMIN_SIGNALS = data;
    renderAdminTable();
  }} catch(e){{
    alert('Errore caricamento dal backend: ' + e.message);
  }}
}}

function renderAdminTable(){{
  const search = (document.getElementById('admin-search').value||'').toLowerCase();
  const filter = document.getElementById('admin-filter-result').value;
  let filtered = ADMIN_SIGNALS;
  if (search) filtered = filtered.filter(s=>(s.player||'').toLowerCase().includes(search) || (s.opponent||'').toLowerCase().includes(search));
  if (filter === 'open') filtered = filtered.filter(s=>!s.result);
  else if (filter) filtered = filtered.filter(s=>s.result === filter);

  // Mostro più recenti in cima
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
  }}).join('') : '<tr><td colspan="12" style="text-align:center;color:#3f3f46;padding:30px">Nessun segnale</td></tr>';
  document.getElementById('admin-count').textContent = `${{filtered.length}} segnali visibili (su ${{ADMIN_SIGNALS.length}} totali)`;
}}

function openEditModal(idx){{
  if (!ADMIN_BACKEND_LIVE) return;
  EDIT_CURRENT_IDX = idx;
  const s = ADMIN_SIGNALS.find(x=>x.idx===idx);
  if (!s){{ alert('Segnale non trovato'); return; }}
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
  else pnl = '— (segnale aperto)';
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
      alert('Errore: ' + (data.error||'sconosciuto'));
      return;
    }}
    closeEditModal();
    await reloadFromBackend();
  }} catch(e){{
    alert('Errore di rete: ' + e.message);
  }}
}}

async function confirmDelete(idx){{
  if (!ADMIN_BACKEND_LIVE) return;
  const s = ADMIN_SIGNALS.find(x=>x.idx===idx);
  if (!s) return;
  if (!confirm(`Cancellare definitivamente il segnale #${{idx}} (${{s.player}} - ${{s.date}} ${{s.signal_time}})?\\n\\nIl balance progressivo verrà ricalcolato.`)) return;
  try {{
    const r = await fetch(`/api/signal/${{idx}}`, {{method: 'DELETE'}});
    const data = await r.json();
    if (!data.ok){{
      alert('Errore: ' + (data.error||'sconosciuto'));
      return;
    }}
    await reloadFromBackend();
  }} catch(e){{
    alert('Errore di rete: ' + e.message);
  }}
}}

// === Init ===
renderKPIs();
renderMonthly();
renderDaily();
populateDays();
renderPositions();
renderSignals();
onStakeModelChange();
renderPivot();
detectBackend().then(()=>renderAdminTable());
</script>
</body>
</html>"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[Dashboard] Generata: {OUTPUT_HTML}")
    return OUTPUT_HTML


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    data = generate_data()
    path = generate_html(data)

    if not args.no_open:
        webbrowser.open(f"file:///{os.path.abspath(path)}")
