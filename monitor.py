"""
monitor.py - Main scheduling loop for a live sports-signal bot (portfolio / showcase version).

WHAT'S REDACTED
================
The actual statistical trigger that decides which match is a "signal" has been
removed and replaced by `evaluate_signal_condition()`, a stub. In the real
system this function encodes a proprietary pattern derived from each player's
running record within a round-robin group; here it's left as an example
placeholder so the file still runs end-to-end conceptually without exposing
the edge.

Everything else is the real architecture: a poll/settle loop aligned to a
fixed daily schedule, a "pending" retry queue for signals whose supporting
data isn't complete yet, per-block dedup so a flaky poll never double-fires,
automatic PnL settlement once a match result comes in, and dashboard/Telegram
side-effects. That's the part meant to be read.
"""
import os
import time
import argparse
from datetime import datetime, date
from dotenv import load_dotenv

from scraper      import fetch_all_events_today, fetch_events_by_ids, fetch_odds_for_events, parse_event
from database     import (
    save_match, get_finished_matches_at, build_standings,
    log_signal, settle_signal, print_summary,
    get_open_signals, auto_settle_signal, _load_matches, _save_matches,
)
from telegram_bot import send_message

load_dotenv()
DEFAULT_STAKE = float(os.getenv("DEFAULT_STAKE", "10"))

# === FIXED SCHEDULE ===
# Four daily blocks. For each block, a window of earlier "history" matches is
# used to compute a per-player state, and that state decides whether a signal
# fires on the upcoming matches at :00 and :30 of the block hour.
BLOCK_HISTORY = {
    10: [(8, 0), (8, 30), (9, 0), (9, 30)],
    14: [(12, 0), (12, 30), (13, 0), (13, 30)],
    18: [(16, 0), (16, 30), (17, 0), (17, 30)],
    22: [(20, 0), (20, 30), (21, 0), (21, 30)],
}

BLOCK_SIGNAL_TIMES = {
    10: [(10, 0), (10, 30)],
    14: [(14, 0), (14, 30)],
    18: [(18, 0), (18, 30)],
    22: [(22, 0), (22, 30)],
}

# Two shots per block: an early one (more time to act on it) and a final one
# right before kickoff, for matches whose history closed late. The dedup
# tracker below prevents the same match firing twice.
SIGNAL_TRIGGERS = {
    "09:55": 10, "09:58": 10,
    "13:55": 14, "13:58": 14,
    "17:55": 18, "17:58": 18,
    "21:55": 22, "21:58": 22,
}

# Recovery trigger at the top of the block hour — only runs if the pending
# queue is still non-empty after the FINAL shot, to retry signals whose
# history hadn't closed yet.
RETRY_TRIGGERS = {
    "10:00": 10,
    "14:00": 14,
    "18:00": 18,
    "22:00": 22,
}

EARLY_TRIGGERS = {"09:55", "13:55", "17:55", "21:55"}
FINAL_TRIGGERS = {"09:58", "13:58", "17:58", "21:58"}

# Hours in which polling happens at all (kept narrow to reduce load).
MONITORING_HOURS = {8, 9, 10, 12, 13, 14, 16, 17, 18, 20, 21, 22}


# ---------- REDACTED STRATEGY HOOK ----------

def evaluate_signal_condition(player_record, opponent_record):
    """
    STRATEGY LOGIC REDACTED.

    In the production system, this inspects each player's running win/loss
    record within the current round-robin group (computed from
    `history_matches`) and returns which side, if any, is worth flagging.

    Contract kept for illustration:
      player_record / opponent_record: dict with "wins" / "losses" (and
      whatever other derived fields the real classifier needs).
      Returns: True if this side should be signaled, else False.

    Swap in your own condition here to run the pipeline end-to-end with a
    different (or no) edge.
    """
    raise NotImplementedError(
        "Signal logic is proprietary and not included in this showcase copy."
    )


# ---------- POLL ----------

_first_poll_done = False


def do_poll():
    """Fetch today's events and persist newly finished ones. First poll sets a baseline."""
    global _first_poll_done
    print(f"\n[Poll @ {datetime.now().strftime('%H:%M:%S')}]")
    raw = fetch_all_events_today()

    if not _first_poll_done:
        db = _load_matches()
        date_str = date.today().isoformat()
        if date_str not in db:
            db[date_str] = {}
        baseline_count = 0
        for e in raw:
            parsed = parse_event(e)
            if parsed["status"] == "finished":
                eid = str(parsed["event_id"])
                db[date_str][eid] = {
                    "event_id":   parsed["event_id"],
                    "time":       parsed["start_dt"].strftime("%H:%M"),
                    "hour":       parsed["hour"],
                    "minute":     parsed["minute"],
                    "status":     "finished",
                    "winnerCode": parsed.get("winnerCode"),
                    "home_id":    parsed["home_id"],
                    "home_name":  parsed["home_name"],
                    "away_id":    parsed["away_id"],
                    "away_name":  parsed["away_name"],
                    "home_sets":  parsed["home_sets"],
                    "away_sets":  parsed["away_sets"],
                    "updated":    datetime.now().isoformat(),
                    "_baseline":  True,
                }
                baseline_count += 1
        _save_matches(db)
        _first_poll_done = True
        print(f"[Poll] Baseline initialized ({baseline_count} already-finished matches ignored).")
        return raw

    new_count = 0
    newly_finished_eids = []
    for e in raw:
        parsed = parse_event(e)
        if save_match(parsed):
            new_count += 1
            newly_finished_eids.append(parsed["event_id"])
    print(f"[Poll] {new_count} newly finished matches recorded.")

    if newly_finished_eids:
        _settle_by_eids(raw, newly_finished_eids)

    process_pending_signals(raw)

    return raw


def _settle_by_eids(raw_events, event_ids):
    """Settle open signals for matches that just finished, using data already fetched."""
    open_sigs = get_open_signals()
    if not open_sigs:
        return

    open_eids = {int(r.get("event_id")): (idx, r) for idx, r in open_sigs if r.get("event_id")}
    raw_by_id = {e.get("id"): e for e in raw_events}

    for eid in event_ids:
        if eid not in open_eids or eid not in raw_by_id:
            continue
        parsed = parse_event(raw_by_id[eid])
        if parsed["status"] != "finished":
            continue

        idx, sig_row = open_eids[eid]
        result = auto_settle_signal(
            event_id=eid,
            home_sets=parsed["home_sets"],
            away_sets=parsed["away_sets"],
            winner_code=parsed.get("winnerCode"),
        )
        if not result:
            continue
        idx_r, pnl, outcome, notes = result
        player_name = sig_row.get("player", "?")
        icon = "WIN" if outcome == "W" else "LOSS"
        send_message(f"{icon} {player_name} | PnL {pnl:+.2f}")

        try:
            from dashboard import generate_data, generate_html
            generate_html(generate_data())
        except Exception:
            pass


# ---------- SETTLEMENT ----------

def do_settle_open_signals():
    """Check all still-open signals, fetch updated results, settle and compute PnL."""
    open_sigs = get_open_signals()
    if not open_sigs:
        return

    event_ids = [int(r.get("event_id")) for _, r in open_sigs if r.get("event_id")]
    if not event_ids:
        return

    events_data = fetch_events_by_ids(event_ids)

    for idx, sig_row in open_sigs:
        eid = int(sig_row.get("event_id") or 0)
        if eid not in events_data:
            continue
        parsed = parse_event(events_data[eid])
        if parsed["status"] != "finished":
            continue

        save_match(parsed)
        result = auto_settle_signal(
            event_id=eid,
            home_sets=parsed["home_sets"],
            away_sets=parsed["away_sets"],
            winner_code=parsed.get("winnerCode"),
        )
        if not result:
            continue
        idx_r, pnl, outcome, notes = result
        player_name = sig_row.get("player", "?")
        icon = "WIN" if outcome == "W" else "LOSS"
        send_message(f"{icon} {player_name} | PnL {pnl:+.2f}")

    try:
        from dashboard import generate_data, generate_html
        generate_html(generate_data())
    except Exception as e:
        print(f"[Settle] Dashboard not regenerated: {e}")


# ---------- SIGNALS ----------

def _get_player_record(player_id, history_matches):
    """Count wins/losses/sets for a player across the block's history matches."""
    wins, losses, sets_won, sets_lost = 0, 0, 0, 0
    for m in history_matches:
        if m["home_id"] == player_id:
            if m["home_sets"] > m["away_sets"]:
                wins += 1
            else:
                losses += 1
            sets_won += m["home_sets"]
            sets_lost += m["away_sets"]
        elif m["away_id"] == player_id:
            if m["away_sets"] > m["home_sets"]:
                wins += 1
            else:
                losses += 1
            sets_won += m["away_sets"]
            sets_lost += m["home_sets"]
    return {"wins": wins, "losses": losses, "sets_won": sets_won, "sets_lost": sets_lost}


# State tracking to avoid duplicate signals across the two shots of a block.
_SIGNALED_EIDS_BY_BLOCK = {}

# Queue of signals whose supporting history wasn't complete yet at the FINAL
# shot. Retried on every subsequent poll until ready or until the time cap
# below is hit (protects against betting on a match that's already underway).
PENDING_CAP_MINUTES = 10
_PENDING_SIGNALS = []


def _block_state_key(signal_hour):
    return f"{date.today().isoformat()}_{signal_hour}"


def _mark_signaled(signal_hour, event_id):
    key = _block_state_key(signal_hour)
    _SIGNALED_EIDS_BY_BLOCK.setdefault(key, set()).add(str(event_id))


def _already_signaled(signal_hour, event_id):
    key = _block_state_key(signal_hour)
    return str(event_id) in _SIGNALED_EIDS_BY_BLOCK.get(key, set())


def _add_to_pending(event, signal_hour, history_times):
    eid = event["event_id"]
    for p in _PENDING_SIGNALS:
        if p["event"]["event_id"] == eid:
            return
    _PENDING_SIGNALS.append({
        "event":         event,
        "signal_hour":   signal_hour,
        "history_times": history_times,
        "queued_at":     datetime.now(),
    })


def process_pending_signals(raw_events):
    """
    Retry queued signals whose history has since completed.

    Safeguards: drop if the target match already finished/started elsewhere,
    or if more than PENDING_CAP_MINUTES has elapsed since its kickoff.
    """
    if not _PENDING_SIGNALS:
        return

    raw_by_id = {e.get("id"): e for e in raw_events}
    now = datetime.now()
    still_pending = []
    by_block = {}

    for p in _PENDING_SIGNALS:
        ev = p["event"]
        eid = ev["event_id"]
        target_start = ev["start_dt"]
        sh = p["signal_hour"]

        live = raw_by_id.get(eid)
        live_status = (live.get("status") or {}).get("type", "unknown") if live else "unknown"
        if live_status in ("finished", "canceled", "postponed", "cancelled"):
            continue

        elapsed_min = (now - target_start).total_seconds() / 60
        if elapsed_min > PENDING_CAP_MINUTES:
            continue

        # (Real system re-checks per-player history completeness here.)
        by_block.setdefault(sh, []).append(ev)

    _PENDING_SIGNALS[:] = still_pending

    for sh, events_to_emit in by_block.items():
        do_signals(sh, raw_events=raw_events, is_final_attempt=True, _from_pending=True)


def do_signals(signal_hour, raw_events=None, is_final_attempt=False, _from_pending=False):
    """
    Generate signals for the given block.

    is_final_attempt:
      False (early shot) -> if supporting data is incomplete, leave it for the
                             next trigger.
      True (final shot)  -> if incomplete, queue it in `_PENDING_SIGNALS`
                             (retried by subsequent polls, capped at
                             PENDING_CAP_MINUTES).
    """
    history_times = BLOCK_HISTORY.get(signal_hour)
    signal_times  = BLOCK_SIGNAL_TIMES.get(signal_hour)
    if not history_times or not signal_times:
        return

    history_matches = get_finished_matches_at(history_times)
    if len(history_matches) < 2:
        print("[Signals] Not enough history yet. Skipping.")
        return

    if raw_events is None:
        raw_events = fetch_all_events_today()

    target_set = set(signal_times)
    signal_events = []
    for e in raw_events:
        p = parse_event(e)
        if (p["hour"], p["minute"]) in target_set and p["status"] != "finished":
            signal_events.append(p)

    signal_events = [e for e in signal_events if not _already_signaled(signal_hour, e["event_id"])]
    if not signal_events:
        return

    event_ids = [e["event_id"] for e in signal_events]
    odds_map = fetch_odds_for_events(event_ids)
    standings = build_standings(history_matches)
    player_position = {}
    for gk, ranked in standings.items():
        for i, rec in enumerate(ranked, start=1):
            player_position[rec["player_id"]] = i

    for ev in signal_events:
        hid, aid = ev["home_id"], ev["away_id"]
        h_rec = _get_player_record(hid, history_matches)
        a_rec = _get_player_record(aid, history_matches)

        # --- REDACTED: real system calls evaluate_signal_condition() here to
        #     decide which side (if either) to flag. Placeholder below always
        #     skips, so the pipeline runs but never fires a real signal. ---
        try:
            should_bet_home = evaluate_signal_condition(h_rec, a_rec)
            should_bet_away = evaluate_signal_condition(a_rec, h_rec)
        except NotImplementedError:
            continue

        bet_player_name, bet_side = None, None
        if should_bet_home:
            bet_player_name, bet_side = ev["home_name"], "home"
        elif should_bet_away:
            bet_player_name, bet_side = ev["away_name"], "away"

        if not bet_player_name:
            continue

        odds = odds_map.get(ev["event_id"], {})
        bet_odds = odds.get(bet_side)
        try:
            bet_odds = float(bet_odds) if bet_odds else None
        except Exception:
            bet_odds = None

        time_str = ev["start_dt"].strftime("%H:%M")
        msg = (
            f"SIGNAL — {time_str}\n"
            f"{ev['home_name']} VS {ev['away_name']}\n"
            f"STAKE {DEFAULT_STAKE}\n"
            f"Reference odds: {bet_odds if bet_odds is not None else 'N/A'}\n"
            f"Play: {bet_player_name}"
        )
        send_message(msg)
        log_signal({
            "event_id":    ev["event_id"],
            "date":        date.today().isoformat(),
            "signal_time": time_str,
            "player":      bet_player_name,
            "player_side": bet_side,
            "odds":        bet_odds if bet_odds is not None else "",
            "stake":       DEFAULT_STAKE,
        })
        _mark_signaled(signal_hour, ev["event_id"])

    try:
        from dashboard import generate_data, generate_html
        generate_html(generate_data())
    except Exception as e:
        print(f"[Signals] Dashboard regen failed (non-blocking): {e}")


# ---------- LOOP ----------

def run_loop():
    print("=" * 60)
    print("  SIGNAL BOT — ACTIVE (showcase copy, strategy redacted)")
    print("=" * 60)

    last_trigger = None
    last_poll_minute = None
    last_settle_minute = None

    while True:
        now = datetime.now()
        hm = now.strftime("%H:%M")
        current_minute_key = now.strftime("%H:%M")

        if hm in SIGNAL_TRIGGERS and hm != last_trigger:
            last_trigger = hm
            sh = SIGNAL_TRIGGERS[hm]
            is_final = hm in FINAL_TRIGGERS
            raw = do_poll()
            do_signals(sh, raw_events=raw, is_final_attempt=is_final)
            time.sleep(30)
            continue

        if hm in RETRY_TRIGGERS and hm != last_trigger:
            last_trigger = hm
            if _PENDING_SIGNALS:
                do_poll()
                time.sleep(30)
                continue

        if (now.hour in MONITORING_HOURS
                and now.minute % 3 == 0
                and now.second < 30
                and current_minute_key != last_poll_minute):
            last_poll_minute = current_minute_key
            do_poll()
            time.sleep(25)
            continue

        if (now.minute % 5 == 0
                and now.second < 20
                and current_minute_key != last_settle_minute):
            last_settle_minute = current_minute_key
            try:
                do_settle_open_signals()
            except Exception as e:
                print(f"[Settle] Error: {e}")
            time.sleep(15)
            continue

        time.sleep(15)


# ---------- CLI ----------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll",       action="store_true")
    parser.add_argument("--signals",    type=int)
    parser.add_argument("--autosettle", action="store_true")
    parser.add_argument("--summary",    action="store_true")
    args = parser.parse_args()

    if args.poll:
        do_poll()
    elif args.signals:
        do_signals(args.signals, is_final_attempt=True)
    elif args.autosettle:
        do_settle_open_signals()
    elif args.summary:
        print_summary()
    else:
        try:
            run_loop()
        except KeyboardInterrupt:
            print("\n[Monitor] Stopped.")
