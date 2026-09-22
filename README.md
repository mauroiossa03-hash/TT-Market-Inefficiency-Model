# TT-Market-Inefficiency-Model  INTRODUCTION
While betting may be considered a form of gambling, I designed a strategy based on the financial concept of market making, which generates a statistical edge in a small and inefficient market: table tennis. This repository documents how the strategy works.

I later packaged it into a subscription service, sold to other local bettors. Check it via the following link: try-ten-swart.vercel.app.

The strategy itself is entirely my own; I used Claude to help write the code. To me, this project was an original way to transpose financial knowledge into a simple, accessible environment, and a way to make money while still studying. Building, running, and selling it developed skills in risk management, decision making, entrepreneurship, and working effectively with AI tools.

# WHY IN A FINANCE CV? 
The betting market for a niche, low-liquidity competition behaves a lot like a thinly-traded corner of a financial market: few participants, wide and inconsistent pricing, and a bookmaker who — like a market maker — has to quote a price (odds) before it has fully absorbed the available information. That gap between the posted price and the "true" probability is exactly the kind of inefficiency quantitative trading is built to find and exploit.

This project is the full pipeline of that process, end to end:

Signal research — identifying a repeatable statistical pattern in a dataset (player results within a round-robin group) that the market wasn't pricing in.
Rule-based signal generation — encoding that pattern into a bot that flags candidates automatically, with the final call still reviewed rather than executed blind.
Production infrastructure — live data ingestion, persistent storage, automated monitoring on a fixed schedule, and a PnL dashboard — the same shape as a small systematic trading desk, just running on a smaller market.

The finance skill on display isn't "I bet on ping pong" — it's building and running a data-driven, systematic strategy from raw data to live P&L, and being able to explain honestly what worked, what didn't, and why.

# FILE ORGANIZATION 
- `monitor.py` — orchestration loop: schedule, polling, retries, PnL settlement
- `scraper.py` — live data ingestion, with anti-bot bypass
- `liga_pro_ratings.py` — player rating scraping and caching
- `database.py` — persistence layer (JSON/CSV) with concurrency-safe writes
- `dashboard.py` — HTML dashboard: PnL, standings, signal history
- `telegram_bot.py` — Telegram notifications
- `requirements.txt` — Python dependencies
  
# DOES IT STILL WORK?
No — and the reason is a mundane fact anyone who has placed even one sports bet already knows: a bookmaker has to quote odds on a market before you can bet it. After a period of consistent, correctly-directional signals on Czech Liga Pro, Italian bookmakers stopped quoting odds on that competition altogether. This is the standard fate of a small, illiquid market once a sharp, repeatable edge is spotted on it — bookmakers either limit the accounts exploiting it or, as happened here, pull the market's pricing entirely. Without a quoted price there's nothing to bet into, so the pipeline still runs (it will happily generate signals) but there's no market left to act on them.

# CODE LIMITATIONS
The repository is intentionally incomplete, for economic reasons: the exact rule that turns a player's in-group record into a signal has been stripped out of monitor.py and replaced with a stub (evaluate_signal_condition), and the .env file (credentials) and historical data files (matches_db.json, signals_db.csv, player_ratings.json) are not included. Everything else here — data pipeline, scheduling, retry/dedup logic, dashboarding — is the genuine, unmodified engineering behind the system.
