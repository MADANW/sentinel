# BIRD.md — What Is This Project?

A plain-English explanation of Sentinel for someone who has never seen it before.

---

## The one-sentence version

Every morning, a program runs automatically, looks at the stock market, asks an AI whether today is a good day to buy or sell, and if the answer is confident enough, places a trade — with strict limits so it can never blow up your account.

---

## What problem does it solve?

Trading manually is emotional. You see a red day and panic-sell. You see green and get greedy. sentinel removes you from the moment-to-moment decisions. You set the rules once (in code), and the machine follows them exactly, every single day, without fear or greed.

You still own the strategy. The bot just executes it.

---

## The two halves of the system

### Half 1 — The Python bot (the brain)

This is the part that actually does the thinking and trading. It runs on a Linux computer (a cheap cloud server) every weekday morning at 9:45 AM New York time.

### Half 2 — The MetaTrader 5 EAs (the forex arm)

These are separate trading programs that run inside MetaTrader 5, a popular forex trading platform. They trade currency pairs (USD/JPY, GBP/USD, etc.) using their own technical rules. They are now connected to the Python bot — they check what the bot thinks before entering any trade.

---

## What happens every morning, step by step

Here is exactly what happens when the bot wakes up:

```
9:45 AM ET, Monday–Friday
         |
         v
1. SAFETY CHECK
   The bot checks that nobody tampered with the risk limits overnight.
   If anything looks wrong, it stops immediately.
         |
         v
2. FETCH MARKET DATA
   Downloads ~6 months of daily price history for SPY, QQQ, IWM
   (three big US stock ETFs) from Alpaca (an online brokerage).
         |
         v
3. BUILD FEATURES
   Crunches the price data into 12 numbers that describe the market's
   current behavior. Examples:
     - Is the trend strong or weak? (Hurst exponent)
     - Is the price mean-reverting? (Ornstein-Uhlenbeck stats)
     - What regime is the market in? (trending / random walk / range-bound)
         |
         v
4. GATE 1 — ML MODEL
   Runs those 12 numbers through an XGBoost machine-learning model
   (trained on 4 years of data) that produces a single probability:
     > 0.60 → probably going UP today (bullish)
     < 0.40 → probably going DOWN today (bearish)
     0.40–0.60 → no idea (dead zone — no trade)

   If dead zone: stop. Log it. Done for the day.
         |
         v
5. GATE 2 — MONTE CARLO SIMULATION
   Runs 1,000 simulated versions of "what could happen today" based on
   the market's recent volatility. Checks: does the simulated win rate
   exceed 55%? If not, the math doesn't support the trade.

   If below threshold: stop. Log it. Done.
         |
         v
6. FETCH NEWS HEADLINES
   Pulls the 10 most recent news headlines about the ticker from Alpaca.
         |
         v
7. GATE 3 — CLAUDE REVIEW
   Sends the ML probability, simulation result, and headlines to Claude
   (Anthropic's AI). Claude's only job: approve or veto the trade.
   Claude CANNOT generate a trade signal — it can only say yes or no.
   Its response is sanitized before being trusted.

   If veto: stop. Log it. Done.
         |
         v
8. RISK ENGINE
   Before placing any order, three hardcoded limits are enforced:
     - Never risk more than 1% of the account on a single trade
     - If total losses today exceed 2%, kill switch fires → process exits
     - Maximum 3 trades per day

   These are written directly in code and cannot be changed via config
   files, environment variables, or command-line flags.
         |
         v
9. PLACE THE ORDER
   Sends a bracket order to Alpaca. A bracket order has three parts:
     - Entry: buy/sell at current market price
     - Stop-loss: automatically sell if price moves against you (limits loss)
     - Take-profit: automatically sell if price moves in your favor (locks gains)
         |
         v
10. LOG EVERYTHING
    Every trade, every gate result, every skip reason is written to
    a Supabase database (Postgres in the cloud). The dashboard reads
    from this database.
```

---

## The dashboard

A Next.js web app (React) that you open in a browser. It shows:

- **Today's P&L** — how much money was made or lost today
- **Gate status** — which of the 3 gates passed or failed, and why
- **Trade journal** — every trade, with entry/stop/target/fill prices
- **MT5 backtests tab** — historical backtest results from the MetaTrader EAs
- **Kill-switch banner** — big red warning if the daily loss limit was hit

The dashboard is read-only. It cannot submit orders. It only has a read key to the database.

---

## The MetaTrader 5 EAs

These are two separate programs written in MQL5 (MetaTrader's scripting language) that trade forex pairs inside the MetaTrader 5 platform:

### BB Scalper
- **Strategy:** Mean reversion. When price touches the outer Bollinger Band with high volume, it bets that price will snap back to the middle.
- **Sessions:** Only trades during London (8–12 GMT) and New York (13–17 GMT) sessions, when volume is highest.
- **Optimized:** 3,625 parameter combinations were tested to find the best settings.

### MA Crossover EA
- **Strategy:** Trend following. When the fast moving average crosses above the slow moving average AND the ADX indicator confirms a strong trend, it buys. The reverse for selling.
- **Pair:** USD/JPY.

### How they connect to the Python bot (the bias bridge)

Every morning after the Python pipeline runs, it writes a file called `sentinel-bias.json` that looks like this:

```json
{
  "direction": "bullish",
  "confidence": 0.67,
  "reasoning": "Strong trend alignment across all three gates.",
  "timestamp": "2026-06-15T09:45:00+00:00"
}
```

Before the MQL5 EAs enter any trade, they read this file and check:
1. Is the file less than 8 hours old? (Otherwise it's stale — skip)
2. Is the direction "neutral"? (Pipeline ran but no signal — skip)
3. Does the confidence meet the minimum? (Default 60%)
4. Does the EA's signal agree with the bias? (BB Scalper won't go long if bias is bearish)

If any check fails, the EA skips the trade. The Python brain overrides the EA's local signal.

---

## The three services you need accounts for

| Service | What it does | Cost |
|---|---|---|
| **Alpaca** | Online brokerage. Provides price data and executes stock trades. | Free paper trading account. Live trading requires funded account. |
| **Anthropic** | Claude AI API. Reviews the trade signal before execution. | ~$0.003 per morning run. $5 credit lasts months. |
| **Supabase** | Postgres database in the cloud. Stores every trade and pipeline run. | Free tier is sufficient. |

---

## Security model

The system was built assuming that one compromised file can cause real financial loss. Key decisions:

- **Secrets never in code.** API keys live in `.env` only, which is gitignored.
- **Paper trading by default.** You must explicitly set `TRADING_ENV=live` to risk real money.
- **Risk limits are hardcoded.** They cannot be changed without editing the source code and passing all tests.
- **Claude output is untrusted.** Every response from the AI is parsed through a strict sanitizer before any value is used.
- **Model integrity check.** The XGBoost model file (`classifier.pkl`) is never committed to git. On every run, its SHA-256 hash is verified against a committed anchor before loading. If someone swapped the file, the process exits immediately.
- **Secret scanning on every commit.** A pre-commit hook runs `gitleaks` to catch any accidentally typed API key before it hits git history.

---

## What "paper trading" means

Alpaca has two modes:
- **Paper:** Fake money. Orders are simulated. Nothing real happens. Great for testing.
- **Live:** Real money. Real orders on real markets.

The bot defaults to paper. Every command in this repo uses paper unless you go out of your way to change it. The log line `Account equity: $100000.00 (paper)` confirms you are in the safe mode.

---

## The ML model in plain English

An XGBoost classifier is a type of machine learning model that learns patterns from historical data. Here it was trained like this:

1. Take 4 years of daily price data for SPY, QQQ, and IWM.
2. For each trading day, compute 12 numbers that describe what the market looked like that morning.
3. Label each day: did the price go up (1) or down (0) by end of day?
4. Train the model on the first 80% of days, test on the last 20% (never mixing the two — that would be cheating).
5. The model learned which combinations of those 12 numbers tend to precede up days vs down days.

Its accuracy (AUC-ROC: 0.5623) sounds low, but a perfect coin flip is 0.50. Anything above 0.55 is considered a real edge in finance — markets are hard to predict. The model is a gate, not a crystal ball.

---

## Directory map

```
sentinel/
│
├── main.py                       ← Run this every morning
│
├── backend/
│   ├── core/
│   │   ├── morning_pipeline.py   ← Orchestrates all 3 gates
│   │   ├── risk_engine.py        ← Hardcoded limits + kill switch
│   │   ├── feature_engineering.py← Computes the 12 ML features
│   │   ├── model.py              ← Loads + runs XGBoost (with hash check)
│   │   ├── monte_carlo.py        ← Gate 2: 1,000 price simulations
│   │   ├── bias_validator.py     ← Sanitizes Claude's response
│   │   ├── bias_writer.py        ← Writes bias JSON for MT5 EAs
│   │   ├── order_executor.py     ← Places bracket orders on Alpaca
│   │   ├── journal.py            ← Logs trades to Supabase
│   │   └── signals.py            ← EMA crossover detection
│   │
│   ├── scripts/
│   │   ├── train_model.py        ← Retrain the XGBoost classifier
│   │   ├── monitor.py            ← Polls Alpaca fills → closes journal entries
│   │   └── import_backtests.py   ← Imports MT5 xlsx reports → Supabase
│   │
│   └── tests/                    ← 139 tests
│
├── mql5/
│   ├── experts/
│   │   ├── bb_scalper.mq5        ← Bollinger Bands forex EA
│   │   ├── bb_scalper-LS1.mq5   ← Same EA, larger lot size
│   │   └── MACrossoverEA.mq5    ← MA Crossover forex EA
│   ├── include/
│   │   └── SentinelBias.mqh      ← Shared bias file reader (used by both EAs)
│   └── backtests/               ← MT5 Strategy Tester result files
│
├── dashboard/                   ← Next.js web app (read-only UI)
│
├── supabase/
│   └── migrations/              ← SQL schemas: trades, pipeline_runs, ea_backtests
│
├── systemd/                     ← Copy to ~/.config/systemd/user/ on the server
│   ├── sentinel.service         ← Runs main.py once
│   ├── sentinel.timer           ← Fires weekdays at 9:45 AM ET
│   └── sentinel-monitor.service ← Keeps monitor.py running
│
├── models/
│   ├── classifier.pkl           ← NOT committed (security). Generate locally.
│   └── classifier.sha256        ← Committed. Integrity anchor.
│
└── docs/                        ← You are here
    ├── BIRD.md                  ← This file
    ├── BRAIN.md                 ← Detailed project status + decisions log
    ├── RUNNING.md               ← Step-by-step setup guide
    ├── SCHEDULING.md            ← How to set up the systemd timer on a server
    └── SECURITY.md              ← Threat model + incident response
```

---

## The daily routine, summarized

| Time | What happens |
|---|---|
| 9:45 AM ET | `sentinel.timer` fires `main.py` on the server |
| 9:45–9:46 | Pipeline runs: data → ML → Monte Carlo → Claude → order |
| Throughout day | `monitor.py` polls every 60s for fills → closes journal entries |
| Anytime | Dashboard at `localhost:3000` shows current state |
| After market close | Check dashboard. No action needed. |
| Quarterly | Retrain the model with fresh data |
