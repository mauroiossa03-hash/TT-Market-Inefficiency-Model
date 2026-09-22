"""
scraper.py - Scraping Sofascore Czech Liga Pro Tennis Tavolo

FIX 2026-06-22 (v2): SofaScore/Cloudflare ora valida anche il TLS fingerprint
(JA3). `requests` standard è bloccato anche con X-Requested-With perché la
firma TLS di OpenSSL non corrisponde a Chrome. Uso `curl_cffi` che impersona
la firma TLS di Chrome reale.

Strategia:
  1) PRIMARY: curl_cffi con impersonate="chrome" (no Chrome process)
  2) FALLBACK: Selenium CDP solo se la chiamata diretta fallisce
La scraping quote (`fetch_odds_for_events`) rimane DOM-based via Selenium.
"""
import json
import time
import secrets
from datetime import datetime, date

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# curl_cffi è obbligatorio per bypassare Cloudflare TLS fingerprinting.
# Se non installato: py -m pip install curl_cffi
try:
    from curl_cffi import requests as cffi_requests
    _HAS_CFFI = True
except ImportError:
    _HAS_CFFI = False
    print("[Scraper] ⚠️  curl_cffi non installato — fallback Selenium-only.")
    print("[Scraper]    Installa con: py -m pip install curl_cffi")

TOURNAMENT_ID  = 19039
BASE_URL       = "https://api.sofascore.com/api/v1"
TOURNAMENT_URL = f"https://www.sofascore.com/tournament/table-tennis/czech-republic/czech-liga-pro/{TOURNAMENT_ID}"

# Impersona Chrome recente. Se questo valore smettesse di funzionare,
# provare "chrome124", "chrome120", "chrome116" in cascata.
_IMPERSONATE = "chrome"


def _build_driver(intercept=False):
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1920,1080")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    if intercept:
        opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


def _js_fetch(driver, url):
    """Fetch in-browser con header X-Requested-With."""
    xrw = secrets.token_hex(8)
    script = """
        var cb = arguments[arguments.length - 1];
        fetch('""" + url + """', {
            credentials: 'include',
            headers: {'X-Requested-With': '""" + xrw + """'}
        })
            .then(function(r){ return r.json(); })
            .then(function(d){ cb(d); })
            .catch(function(e){ cb({error: e.toString()}); });
    """
    return driver.execute_async_script(script)


def _api_get(path):
    """
    Chiama l'API SofaScore con curl_cffi (TLS fingerprint Chrome).
    Bypassa Cloudflare sia a livello JA3 che a livello header.
    """
    if not _HAS_CFFI:
        return {"error": "curl_cffi non installato"}

    url = BASE_URL + path
    headers = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.sofascore.com/",
        "Origin": "https://www.sofascore.com",
        "X-Requested-With": secrets.token_hex(8),
    }
    try:
        r = cffi_requests.get(url, headers=headers, impersonate=_IMPERSONATE, timeout=15)
        if r.status_code == 200:
            return r.json()
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def fetch_all_events_today():
    """Restituisce tutti gli eventi Czech Liga Pro di oggi."""
    date_str = date.today().strftime("%Y-%m-%d")
    print(f"[Scraper] Fetch eventi @ {date_str}...")

    events = []
    api_ok = False

    # ── PRIMARY: curl_cffi con TLS Chrome ──────────────────────────────
    data = _api_get(f"/sport/table-tennis/scheduled-events/{date_str}")
    if isinstance(data, dict) and "events" in data:
        api_ok = True
        for e in data["events"]:
            tid = e.get("tournament", {}).get("uniqueTournament", {}).get("id")
            if tid == TOURNAMENT_ID:
                events.append(e)
        print(f"[Scraper] API diretta OK: {len(events)} eventi.")
    else:
        err = data.get("error", "?") if isinstance(data, dict) else "tipo sconosciuto"
        print(f"[Scraper] API diretta fallita ({err}) → fallback Selenium...")

    # ── FALLBACK: Selenium CDP ─────────────────────────────────────────
    if not api_ok:
        driver = _build_driver(intercept=True)
        try:
            driver.execute_cdp_cmd("Network.enable", {})
            driver.get(TOURNAMENT_URL)
            time.sleep(8)

            logs = driver.get_log("performance")
            for entry in logs:
                try:
                    msg = json.loads(entry["message"])["message"]
                    if msg.get("method") != "Network.responseReceived":
                        continue
                    url = msg.get("params", {}).get("response", {}).get("url", "")
                    if "sofascore.com/api" not in url:
                        continue
                    if not any(k in url for k in ("events", "scheduled", "matches", "tournament")):
                        continue
                    req_id = msg["params"].get("requestId")
                    try:
                        body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": req_id})
                        body_data = json.loads(body.get("body", "{}"))
                        for e in body_data.get("events", []):
                            tid = e.get("tournament", {}).get("uniqueTournament", {}).get("id")
                            if tid == TOURNAMENT_ID:
                                events.append(e)
                    except Exception:
                        pass
                except Exception:
                    pass

            if not events:
                result = _js_fetch(driver, f"{BASE_URL}/sport/table-tennis/scheduled-events/{date_str}")
                if result and "events" in result:
                    for e in result["events"]:
                        tid = e.get("tournament", {}).get("uniqueTournament", {}).get("id")
                        if tid == TOURNAMENT_ID:
                            events.append(e)
        finally:
            driver.quit()

    # ── dedup ──────────────────────────────────────────────────────────
    seen, unique = set(), []
    for e in events:
        eid = e.get("id")
        if eid and eid not in seen:
            seen.add(eid)
            unique.append(e)

    print(f"[Scraper] Trovati {len(unique)} eventi.")
    return unique


def fetch_events_by_ids(event_ids):
    """
    Recupera i dati aggiornati di specifici eventi.
    PRIMARY: curl_cffi diretto.
    FALLBACK: Selenium solo per gli event_id che hanno fallito.
    """
    if not event_ids:
        return {}
    print(f"[Scraper] Aggiorno {len(event_ids)} eventi segnalati...")
    results = {}
    failed = []

    # ── PRIMARY: curl_cffi ─────────────────────────────────────────────
    for eid in event_ids:
        data = _api_get(f"/event/{eid}")
        if isinstance(data, dict) and "event" in data:
            results[eid] = data["event"]
        elif isinstance(data, dict) and not data.get("error"):
            results[eid] = data
        else:
            failed.append(eid)

    # ── FALLBACK: Selenium ─────────────────────────────────────────────
    if failed:
        print(f"  [Scraper] Fallback Selenium per {len(failed)}/{len(event_ids)} eventi...")
        driver = _build_driver()
        try:
            driver.get(TOURNAMENT_URL)
            time.sleep(5)
            for eid in failed:
                try:
                    data = _js_fetch(driver, f"{BASE_URL}/event/{eid}")
                    if data and "event" in data:
                        results[eid] = data["event"]
                    elif data and not data.get("error"):
                        results[eid] = data
                except Exception as ex:
                    print(f"  [Event {eid}] errore: {ex}")
        finally:
            driver.quit()

    return results


def _fraction_to_decimal(frac_str):
    """Converte quota frazionaria (es. '13/8') in decimale (es. 2.63)."""
    if not frac_str:
        return None
    try:
        frac_str = str(frac_str).strip()
        if "/" in frac_str:
            num, den = frac_str.split("/")
            return round(float(num) / float(den) + 1, 2)
        else:
            return round(float(frac_str), 2)
    except Exception:
        return None


def fetch_odds_for_events(event_ids):
    """
    Recupera quote bet365 per una lista di eventi.
    Naviga sulla pagina dell'evento SofaScore e legge le quote dal DOM.
    Su SofaScore, bet365 è SEMPRE il primo bookmaker mostrato.
    """
    if not event_ids:
        return {}
    driver = _build_driver()
    results = {}
    try:
        for eid in event_ids:
            odds = {"home": None, "away": None}
            try:
                driver.get(f"https://www.sofascore.com/event/{eid}")
                time.sleep(5)

                text = driver.execute_script("return document.body.innerText;")

                import re
                text_lower = text.lower()
                ft_idx = -1
                for marker in ["full-time", "full time", "risultato finale"]:
                    ft_idx = text_lower.find(marker)
                    if ft_idx > -1:
                        break

                if ft_idx > -1:
                    after = text[ft_idx:ft_idx + 300]
                    nums = re.findall(r'\d+\.\d{2}', after)
                    if len(nums) >= 2:
                        odds["home"] = float(nums[0])
                        odds["away"] = float(nums[1])

            except Exception as ex:
                print(f"  [Odds] {eid}: errore → {ex}")

            results[eid] = odds
            print(f"  [Odds] {eid}: home={odds['home']} away={odds['away']}")
    finally:
        driver.quit()
    return results


def parse_event(event):
    """Estrae i campi utili di un evento."""
    start_ts = event.get("startTimestamp", 0)
    start_dt = datetime.fromtimestamp(start_ts)
    home = event.get("homeTeam", {})
    away = event.get("awayTeam", {})
    return {
        "event_id":  event.get("id"),
        "start_dt":  start_dt,
        "hour":      start_dt.hour,
        "minute":    start_dt.minute,
        "status":    event.get("status", {}).get("type", "unknown"),
        "winnerCode": event.get("winnerCode"),
        "home_id":   home.get("id"),
        "home_name": home.get("name", ""),
        "away_id":   away.get("id"),
        "away_name": away.get("name", ""),
        "home_sets": event.get("homeScore", {}).get("current", 0) or 0,
        "away_sets": event.get("awayScore", {}).get("current", 0) or 0,
    }
