"""
database.py - DB locale partite e segnali.
Salva su file JSON + CSV.
"""
import json
import csv
import os
from datetime import datetime, date
from collections import defaultdict
from contextlib import contextmanager

# Filelock opzionale: se non installato, le scritture funzionano lo stesso ma
# senza protezione contro race condition (rischio se monitor.py + dashboard_server
# scrivono nello stesso istante). Per installarlo:  pip install filelock
try:
    from filelock import FileLock, Timeout
    _HAS_FILELOCK = True
except ImportError:
    _HAS_FILELOCK = False

HERE         = os.path.dirname(os.path.abspath(__file__))
MATCHES_JSON = os.path.join(HERE, "matches_db.json")
SIGNALS_CSV  = os.path.join(HERE, "signals_db.csv")
SIGNALS_LOCK = os.path.join(HERE, "signals_db.csv.lock")


@contextmanager
def _signals_lock(timeout=10):
    """
    Context manager per il lock sulle scritture al CSV segnali.
    Se filelock non è disponibile, è un no-op (warning una volta sola).
    """
    if _HAS_FILELOCK:
        lock = FileLock(SIGNALS_LOCK, timeout=timeout)
        with lock:
            yield
    else:
        if not getattr(_signals_lock, "_warned", False):
            print("[DB] ⚠ filelock non installato — scritture concorrenti non protette. Esegui: pip install filelock")
            _signals_lock._warned = True
        yield

SIGNALS_FIELDS = [
    "event_id","date","signal_time","player","player_side","opponent","group_id",
    "player_wins","player_losses","opp_wins","opp_losses",
    "odds","stake","result","pnl","balance","notes"
]


def _load_matches():
    if not os.path.exists(MATCHES_JSON):
        return {}
    with open(MATCHES_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_matches(db):
    with open(MATCHES_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, default=str)


def save_match(parsed_event):
    """Salva una partita. Restituisce True se è una nuova partita finita."""
    db = _load_matches()
    date_str = date.today().isoformat()
    if date_str not in db:
        db[date_str] = {}

    eid = str(parsed_event["event_id"])
    was_finished = db[date_str].get(eid, {}).get("status") == "finished"
    is_finished  = parsed_event["status"] == "finished"

    record = {
        "event_id":   parsed_event["event_id"],
        "time":       parsed_event["start_dt"].strftime("%H:%M"),
        "hour":       parsed_event["hour"],
        "minute":     parsed_event["minute"],
        "status":     parsed_event["status"],
        "winnerCode": parsed_event.get("winnerCode"),
        "home_id":    parsed_event["home_id"],
        "home_name":  parsed_event["home_name"],
        "away_id":    parsed_event["away_id"],
        "away_name":  parsed_event["away_name"],
        "home_sets":  parsed_event["home_sets"],
        "away_sets":  parsed_event["away_sets"],
        "updated":    datetime.now().isoformat(),
    }
    # Mantieni il flag baseline se presente
    if "_baseline" in db[date_str].get(eid, {}):
        record["_baseline"] = db[date_str][eid]["_baseline"]

    db[date_str][eid] = record
    _save_matches(db)

    # Hook: se il match è una nuova partita finita (non baseline, prima volta che
    # la registriamo come finita), congela il rating Liga Pro dei due giocatori
    # per la giornata. È un costo trascurabile: dopo il primo lookup, l'indice
    # globale è in memoria e i lookup successivi sono O(1).
    is_new_finished = is_finished and not was_finished
    if is_new_finished and not record.get("_baseline", False):
        try:
            from liga_pro_ratings import freeze_rating
            freeze_rating(parsed_event.get("home_name", ""))
            freeze_rating(parsed_event.get("away_name", ""))
        except Exception as e:
            # Non bloccare il salvataggio del match se il rating fallisce
            print(f"[DB] ⚠ Errore freeze rating: {e}")

    return is_new_finished


def get_finished_matches_at(target_times):
    """
    Restituisce le partite finite agli orari specificati (esatti, non baseline).
    target_times: lista di tuple (hour, minute), es. [(8,0),(8,30),(9,0),(9,30)]
    """
    db = _load_matches()
    date_str = date.today().isoformat()
    day_matches = db.get(date_str, {}).values()
    target_set = set(target_times)
    return [
        m for m in day_matches
        if m.get("status") == "finished"
        and not m.get("_baseline", False)
        and (m.get("hour"), m.get("minute")) in target_set
    ]


def get_match_by_id(event_id):
    """Ritorna i dati di una partita specifica."""
    db = _load_matches()
    date_str = date.today().isoformat()
    return db.get(date_str, {}).get(str(event_id))


# ---------- STANDINGS ----------

def build_standings(matches):
    """
    Dato un insieme di partite, costruisce la classifica per girone.
    Usa Union-Find: due giocatori nello stesso girone se si sono incontrati.
    """
    parent = {}
    def find(x):
        if x not in parent:
            parent[x] = x
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for m in matches:
        hid, aid = m.get("home_id"), m.get("away_id")
        if hid and aid:
            union(hid, aid)

    records = defaultdict(dict)
    for m in matches:
        hid, aid = m["home_id"], m["away_id"]
        hname, aname = m["home_name"], m["away_name"]
        hs, as_ = m["home_sets"], m["away_sets"]
        hw, aw = (1,0) if hs > as_ else ((0,1) if as_ > hs else (0,0))
        gk = find(hid)

        for pid, pname, w, l, sf, sa in [
            (hid, hname, hw, aw, hs, as_),
            (aid, aname, aw, hw, as_, hs),
        ]:
            if pid not in records[gk]:
                records[gk][pid] = {
                    "player_id": pid, "player_name": pname,
                    "wins":0, "losses":0, "sets_won":0, "sets_lost":0
                }
            records[gk][pid]["wins"]      += w
            records[gk][pid]["losses"]    += l
            records[gk][pid]["sets_won"]  += sf
            records[gk][pid]["sets_lost"] += sa

    # Ordinamento: vittorie desc, poi set vinti desc, poi differenza set desc
    return {gk: sorted(p.values(),
                       key=lambda r: (-r["wins"], -r["sets_won"], -(r["sets_won"]-r["sets_lost"])))
            for gk, p in records.items()}


# ---------- SIGNALS ----------

def _init_signals_csv():
    if not os.path.exists(SIGNALS_CSV):
        with open(SIGNALS_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=SIGNALS_FIELDS).writeheader()


def log_signal(data):
    _init_signals_csv()
    with _signals_lock():
        with open(SIGNALS_CSV, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=SIGNALS_FIELDS).writerow(
                {k: data.get(k, "") for k in SIGNALS_FIELDS}
            )


def get_open_signals():
    """Ritorna tutti i segnali ancora aperti (senza risultato)."""
    _init_signals_csv()
    with open(SIGNALS_CSV, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [(i+1, r) for i, r in enumerate(rows) if not r.get("result")]


def auto_settle_signal(event_id, home_sets, away_sets, winner_code=None):
    """
    Chiude automaticamente un segnale sulla base del risultato finale.
    home_sets/away_sets: set vinti
    winner_code: 1=home, 2=away (opzionale, inferito dai set)
    Ritorna (idx, pnl, notes) o None se non trovato.
    """
    _init_signals_csv()
    with _signals_lock():
        with open(SIGNALS_CSV, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        # Determina il vincitore
        if winner_code is None:
            if home_sets > away_sets:
                winner_code = 1
            elif away_sets > home_sets:
                winner_code = 2
            else:
                return None

        target_idx = None
        target_row = None
        for i, r in enumerate(rows):
            if str(r.get("event_id")) == str(event_id) and not r.get("result"):
                target_idx = i + 1
                target_row = r
                break
        if target_row is None:
            return None

        player_side = target_row.get("player_side", "home")
        won = (player_side == "home" and winner_code == 1) or \
              (player_side == "away" and winner_code == 2)

        # Parsing robusto: odds può essere "" (segnale emesso senza quota disponibile).
        try:
            odds = float(target_row.get("odds") or 0)
        except (TypeError, ValueError):
            odds = 0.0
        try:
            stake = float(target_row.get("stake") or 0)
        except (TypeError, ValueError):
            stake = 0.0

        if won:
            if odds > 0:
                pnl = round((odds - 1) * stake, 2)
                result = "W"
                notes = f"auto {home_sets}-{away_sets}"
            else:
                # Quota mancante: registriamo l'esito ma PnL=0 finché l'utente
                # non inserisce manualmente la quota dalla dashboard.
                pnl = 0.0
                result = "W"
                notes = f"auto {home_sets}-{away_sets} (quota mancante, PnL da ricalcolare)"
        else:
            if stake > 0:
                pnl = -stake
            else:
                pnl = 0.0
            result = "L"
            notes = f"auto {home_sets}-{away_sets}"
            if odds <= 0:
                notes += " (quota mancante)"

        rows[target_idx - 1]["result"] = result
        rows[target_idx - 1]["pnl"]    = pnl
        rows[target_idx - 1]["notes"]  = notes
        # Ricalcola balance progressivo su tutte le righe
        running = 0.0
        for r in rows:
            try:
                if r.get("pnl") not in ("", None):
                    running += float(r["pnl"])
                    r["balance"] = round(running, 2)
            except Exception:
                pass

        with open(SIGNALS_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SIGNALS_FIELDS)
            w.writeheader()
            w.writerows(rows)

        return (target_idx, pnl, result, notes)


def settle_signal(idx, result, notes=""):
    _init_signals_csv()
    with _signals_lock():
        with open(SIGNALS_CSV, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if idx < 1 or idx > len(rows):
            print(f"Indice {idx} non valido.")
            return
        row = rows[idx-1]
        odds  = float(row.get("odds") or 1)
        stake = float(row.get("stake") or 0)
        result = result.upper().strip()
        if result == "W":
            pnl = round((odds-1)*stake, 2)
        elif result == "L":
            pnl = -stake
        else:
            pnl = 0.0
        row["result"] = result
        row["pnl"] = pnl
        row["notes"] = notes
        # Ricalcola balance
        running = 0.0
        for r in rows:
            try:
                if r.get("pnl") not in ("", None):
                    running += float(r["pnl"])
                    r["balance"] = round(running, 2)
            except Exception:
                pass
        with open(SIGNALS_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SIGNALS_FIELDS)
            w.writeheader()
            w.writerows(rows)
        print(f"Segnale #{idx} → {result} | PnL: {pnl:+.2f}")


def _recompute_pnl_and_balance(rows):
    """
    Ricalcola pnl per ogni riga (dato result+odds+stake) e balance progressivo.
    Modifica le righe in-place. Riga senza result → pnl='', balance non aggiornato.
    """
    running = 0.0
    for r in rows:
        result = (r.get("result") or "").upper().strip()
        try:
            odds = float(r.get("odds") or 0)
        except (TypeError, ValueError):
            odds = 0.0
        try:
            stake = float(r.get("stake") or 0)
        except (TypeError, ValueError):
            stake = 0.0

        if result == "W":
            pnl = round((odds - 1) * stake, 2) if odds > 0 else 0.0
        elif result == "L":
            pnl = round(-stake, 2)
        elif result == "VOID":
            pnl = 0.0
        else:
            # segnale aperto / non chiuso
            r["pnl"] = ""
            r["balance"] = ""
            continue

        r["pnl"] = pnl
        running += pnl
        r["balance"] = round(running, 2)


def get_all_signals():
    """Ritorna tutti i segnali come list[dict] (ordine CSV originale, idx 1-based esterno)."""
    _init_signals_csv()
    with _signals_lock():
        with open(SIGNALS_CSV, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))


def update_signal(idx, **fields):
    """
    Modifica i campi specificati di un segnale e ricalcola PnL + balance progressivo.
    
    idx: 1-based, come visualizzato in dashboard
    fields ammessi: result, odds, stake, notes
        - result: 'W' | 'L' | 'VOID' | '' (stringa vuota → riapri il segnale)
        - odds: float
        - stake: float
        - notes: str
    
    Restituisce dict del segnale aggiornato (con idx) oppure None se idx non esiste.
    Solleva ValueError se i valori non sono validi.
    """
    allowed = {"result", "odds", "stake", "notes"}
    invalid = set(fields.keys()) - allowed
    if invalid:
        raise ValueError(f"Campi non modificabili: {invalid}. Ammessi: {allowed}")

    # Validazione preventiva
    if "result" in fields:
        v = (fields["result"] or "").upper().strip()
        if v not in ("", "W", "L", "VOID"):
            raise ValueError(f"result deve essere W, L, VOID o vuoto. Ricevuto: '{fields['result']}'")
        fields["result"] = v
    for k in ("odds", "stake"):
        if k in fields and fields[k] not in (None, ""):
            try:
                fields[k] = float(fields[k])
                if fields[k] < 0:
                    raise ValueError(f"{k} deve essere >= 0. Ricevuto: {fields[k]}")
            except (TypeError, ValueError) as e:
                raise ValueError(f"{k} deve essere numerico. Ricevuto: '{fields[k]}'") from e

    _init_signals_csv()
    with _signals_lock():
        with open(SIGNALS_CSV, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        if idx < 1 or idx > len(rows):
            return None

        # Applica modifiche alla riga target
        row = rows[idx - 1]
        for k, v in fields.items():
            row[k] = v

        # Ricalcola pnl e balance per tutte le righe (potrebbero essere cambiati per la riga modificata)
        _recompute_pnl_and_balance(rows)

        with open(SIGNALS_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SIGNALS_FIELDS)
            w.writeheader()
            w.writerows(rows)

        return {"idx": idx, **rows[idx - 1]}


def delete_signal(idx):
    """
    Cancella il segnale all'indice 1-based e ricalcola balance progressivo.
    Restituisce il dict del segnale cancellato (con idx) o None se non esiste.
    """
    _init_signals_csv()
    with _signals_lock():
        with open(SIGNALS_CSV, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        if idx < 1 or idx > len(rows):
            return None

        deleted = dict(rows[idx - 1])
        del rows[idx - 1]

        _recompute_pnl_and_balance(rows)

        with open(SIGNALS_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SIGNALS_FIELDS)
            w.writeheader()
            w.writerows(rows)

        return {"idx": idx, **deleted}


def print_summary():
    _init_signals_csv()
    with open(SIGNALS_CSV, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    settled = [r for r in rows if r.get("result") in ("W","L")]
    wins    = [r for r in settled if r["result"] == "W"]
    total   = sum(float(r.get("pnl") or 0) for r in settled)
    wr      = (len(wins)/len(settled)*100) if settled else 0
    print("\n" + "="*45)
    print("   RIEPILOGO PnL — Czech Liga Pro")
    print("="*45)
    print(f"  Segnali totali : {len(rows)}")
    print(f"  Regolati       : {len(settled)}")
    print(f"  Vinti / Persi  : {len(wins)} / {len(settled)-len(wins)}")
    print(f"  Win rate       : {wr:.1f}%")
    print(f"  PnL totale     : {total:+.2f} unità")
    print("="*45 + "\n")
