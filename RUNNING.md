# Running algo-bot — Operator Guide

End-to-end walkthrough for getting `algo-bot` running on your own machine: from cloning the repo to submitting paper trades and viewing them on the dashboard. Written for someone who has never run the bot before.

> **Safety first.** `TRADING_ENV=paper` is the default. Every step in this guide uses paper trading. Do not switch to `live` until you have watched several weeks of paper runs and read [SECURITY.md](SECURITY.md) end-to-end.

---

## 0. What you'll end up with

After following this guide you will have:

1. A Python virtualenv with pinned, hash-verified dependencies.
2. A trained XGBoost classifier at `models/classifier.pkl` (local-only) plus `models/classifier.sha256` (committed integrity anchor).
3. A Supabase project with two tables: `trades` and `pipeline_runs`.
4. A working `python main.py --ticker SPY` that runs the full morning pipeline and logs to Supabase.
5. A local Next.js dashboard at `http://localhost:3000` showing P&L, gate status, and the trade journal.

Expected setup time: **30–60 minutes** if you already have Python 3.12 and Node 20+ installed. Budget longer if you have to create Alpaca / Anthropic / Supabase accounts from scratch.

---

## 1. Prerequisites

### 1.1 Accounts (all free tier)

| Service | Purpose | Sign up |
|---|---|---|
| [Alpaca](https://alpaca.markets) | Paper brokerage + OHLCV + news | Paper keys are free; no card required |
| [Anthropic](https://console.anthropic.com) | Claude API for bias review | Requires $5 minimum credit |
| [Supabase](https://supabase.com) | Postgres DB + trade journal | Free tier is fine |

### 1.2 Software

- **Python 3.12** (`python --version` should show `3.12.x`)
- **Node.js 20+** and **npm 10+** (`node --version`, `npm --version`)
- **git** (`git --version`)
- A shell that can `source .env`: zsh (macOS default) or bash

### 1.3 Hardware

Training takes ~30 seconds on a laptop with 8 GB RAM. No GPU required.

---

## 2. Clone and install

```bash
git clone https://github.com/<you>/algo-bot.git
cd algo-bot
```

### 2.1 Python virtualenv

```bash
python -m venv .venv
source .venv/bin/activate          # zsh/bash — Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install --require-hashes -r backend/requirements.txt
```

`--require-hashes` means pip refuses to install any package whose SHA-256 does not match what's in `requirements.txt`. If this step fails, you have a network MITM or the lockfile is corrupt — do **not** work around it by dropping the flag.

### 2.2 Pre-commit hooks (recommended)

```bash
pip install pre-commit
pre-commit install
```

This scans every commit with `gitleaks` for accidental secrets. Very hard to leak an API key once this is installed.

### 2.3 Verify tests pass

```bash
pytest backend/tests/ -q
```

Expect **139 passing, 0 failing**. If anything fails here, stop and fix it before adding credentials.

---

## 3. Get your API keys

### 3.1 Alpaca (paper)

1. Log in at https://alpaca.markets → **Paper Trading** (toggle in the header).
2. Right sidebar → **API Keys** → **Generate New Key**.
3. Copy **API Key ID** and **Secret Key**. The secret is shown **once** — save it now.
4. Paper keys look like: `PKxxxxxxxxxxxxxxxxxxxx` / `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.

Alpaca free tier only includes the **IEX** data feed, not SIP. The bot defaults to IEX; you don't need to change anything. If you later upgrade to a paid data plan, set `ALPACA_DATA_FEED=sip` in `.env`.

### 3.2 Anthropic

1. https://console.anthropic.com → **API Keys** → **Create Key**.
2. Copy the key (starts with `sk-ant-...`). Shown once.
3. Add at least $5 to the account so calls don't fail with a balance error. Each morning-pipeline run uses <1k tokens (≈ $0.003), so $5 lasts a long time.

### 3.3 Supabase

1. https://supabase.com → **New Project**. Choose any region and a strong database password (save it in your password manager).
2. Wait ~2 minutes for provisioning.
3. **Project Settings → API**: copy
   - **Project URL** (`https://xxxxxxxxxxxxxxxx.supabase.co`)
   - **anon public** key
   - **service_role** key (⚠️ full DB access — keep it server-side only, never put it in frontend code)

---

## 4. Configure `.env`

The repo ships an `.env.example` template. Copy it and fill in the real values:

```bash
cp .env.example .env
```

Your `.env` should look like this (real values, no quotes):

```bash
# Trading environment — paper is safe default
TRADING_ENV=paper

# Alpaca — paper keys from step 3.1
ALPACA_API_KEY=PKxxxxxxxxxxxxxxxxxxxx
ALPACA_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_FEED=iex                       # free-tier default

# Anthropic — key from step 3.2
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Supabase — URL + service role key from step 3.3
SUPABASE_URL=https://xxxxxxxxxxxxxxxxxxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi...
SUPABASE_ANON_KEY=eyJhbGciOi...
```

`.env` is gitignored. Double-check with `git status` — it must **not** appear in the list of tracked or staged files.

Before running any Python command that needs credentials, load the file into your shell:

```bash
set -a && source .env && set +a
```

You can verify with `echo $ALPACA_API_KEY`. Do this once per terminal session.

---

## 5. Apply Supabase migrations

The bot expects two tables: `trades` (one row per submitted order) and `pipeline_runs` (one row per `main.py` invocation, including skip reasons).

**Option A — Supabase web UI (recommended for first-time setup):**

1. Supabase dashboard → **SQL Editor** → **New query**.
2. Paste the contents of `supabase/migrations/001_create_trades.sql`, click **Run**.
3. New query again, paste `supabase/migrations/002_create_pipeline_runs.sql`, click **Run**.
4. **Table Editor** → confirm both tables exist with the expected columns.

**Option B — Supabase CLI:**

```bash
npm install -g supabase
supabase login
supabase link --project-ref <your-project-ref>
supabase db push
```

---

## 6. Train the classifier

The XGBoost model is not committed (pickle files can execute arbitrary code on load — see `BRAIN.md → Pickle Security Note`). Each operator generates their own locally.

```bash
# Shell must have .env loaded (step 4)
python -m backend.scripts.train_model --tickers SPY,QQQ,IWM --days 756
```

What this does:

1. Fetches ~3 years of daily OHLCV from Alpaca for each ticker.
2. Builds 12-feature rows: 8 momentum/volume indicators plus Hurst exponent, OU log-half-life, OU z-score, regime label.
3. Splits **per ticker** into train/test chronologically (never across tickers — would introduce lookahead at the seam).
4. Trains XGBoost and evaluates on the held-out test set.
5. Gate: **AUC-ROC must be ≥ 0.55** or the model is rejected and nothing is written.
6. On success, writes `models/classifier.pkl` and `models/classifier.sha256`.

Expected log tail:

```
INFO train_model — Evaluation — AUC-ROC: 0.5623 | Accuracy: 0.5202 | ...
INFO train_model — AUC-ROC 0.5623 passes minimum bar of 0.5500 — model accepted.
INFO train_model — Model saved to models/classifier.pkl
INFO train_model — SHA-256 hash saved to models/classifier.sha256
```

Sanity check:

```bash
python -c "import pickle; m=pickle.load(open('models/classifier.pkl','rb')); print('features:', m.n_features_in_)"
# → features: 12

cat models/classifier.sha256
# → 64 hex chars — this is what model.py verifies against on every load
```

If AUC is below 0.55, the gate will refuse to save and `main.py` will fail at model-load time. Try `--days 1008` (4 years) or add more tickers. If it still fails, that's a real signal the feature set has degraded — dig in rather than lowering the gate.

### When to retrain

- Any change to `feature_engineering.py` (column order, window sizes, thresholds).
- Quarterly is a reasonable cadence to keep the model fresh.
- After a period of large regime change (e.g. FOMC pivot, major market event).

Always commit the new `models/classifier.sha256` — it's the integrity anchor the runtime uses. `models/classifier.pkl` stays gitignored.

---

## 7. Run the morning pipeline

```bash
# Fresh terminal — reload env if needed
set -a && source .env && set +a

python main.py --ticker SPY
```

### 7.1 What happens, gate by gate

```
[startup]   assert_constants_unchanged()    ← risk constant tampering check
[account]   fetch_account_equity()          ← Alpaca paper account equity
[price]     fetch_current_price(SPY)        ← latest trade price
[data]      fetch_ohlcv(SPY, days=120)      ← ~6 months of daily bars
[features]  build_features(ohlcv)           ← 12-feature row
[GATE 1]    predict_direction(features)     ← XGBoost probability
              ├─ > 0.60 → bullish
              ├─ < 0.40 → bearish
              └─ else   → dead zone (no trade, exit 0)
[GATE 2]    monte_carlo.simulate(...)       ← 10k GBM paths
              └─ hit_target_rate must exceed threshold
[news]      fetch_headlines(SPY, limit=10)  ← sanitized Alpaca news
[GATE 3]    claude.review(features+news)    ← bias_validator.parse_claude_review
              └─ must return approve=true
[risk]      validate_order(...)             ← 1% risk, 3 trades/day, kill switch
[submit]    submit_bracket_order(...)       ← Alpaca bracket (entry + stop + TP)
[journal]   log_trade(...) / log_pipeline_run(...)  ← Supabase writes
```

Any gate failure is a **clean no-trade** — exit code 0, not 1. Only infrastructure errors (bad credentials, network dead, model hash mismatch) are exit code 1.

### 7.2 Reading the output

A successful bullish run ends with something like:

```
INFO main — Account equity: $100000.00 (paper)
INFO main — ML probability: 0.67 (bullish)
INFO main — Monte Carlo hit-target rate: 0.62
INFO main — Claude review: approved — "Strong trend alignment..."
INFO main — Risk-validated order: qty=3 stop=$519.20 target=$532.80
INFO main — Submitted order id=abc123... status=accepted
INFO main — Logged trade to Supabase
```

A clean no-trade:

```
INFO main — ML probability: 0.52 — dead zone, skipping.
INFO main — Pipeline run logged (skip_reason=ml_dead_zone)
```

### 7.3 Suggested schedule

Run once per trading day, after market open. Simplest cron:

```cron
# Run at 09:35 ET every weekday (5 min after open, gives Alpaca time to stabilize)
35 9 * * 1-5 cd /path/to/algo-bot && source .venv/bin/activate && set -a && source .env && set +a && python main.py --ticker SPY >> ~/algo-bot.log 2>&1
```

`macOS` users: `launchd` is more reliable than cron for machines that sleep. `systemd` timer on Linux is the nicest option.

---

## 8. Dashboard

### 8.1 One-time setup

```bash
cd dashboard
cp .env.local.example .env.local
# Edit .env.local with the Supabase URL + SERVICE_ROLE_KEY from step 3.3
npm ci
```

### 8.2 Run it

```bash
npm run dev
# → http://localhost:3000
```

You'll see:
- **P&L card**: today's realized + open P&L from the `trades` table.
- **Gate status**: latest row from `pipeline_runs` — which gates passed, which failed, and why.
- **Trade table**: all rows from `trades`, newest first.
- **Kill-switch badge**: red if daily loss limit has been hit.

Dashboard is **read-only** — it only has the service role key, never the Alpaca credentials. It cannot submit orders.

### 8.3 Deploy (optional)

For a remote dashboard, `vercel deploy` from `dashboard/` works out of the box. Set the same `.env.local` values as environment variables in the Vercel project settings.

---

## 9. Going live (when you're ready — not yet)

Do not do this until you have:
- [ ] Watched ≥20 paper runs pass cleanly.
- [ ] Read `SECURITY.md` completely.
- [ ] Reviewed `backend/core/risk_engine.py` — understand what each constant does.
- [ ] Decided what happens if the kill switch fires (it calls `sys.exit(1)` — do you have monitoring that pages you?).
- [ ] Funded your live Alpaca account with money you can afford to lose.

To flip:

```bash
# In .env
TRADING_ENV=live
ALPACA_API_KEY=<live key from alpaca.markets, not paper>
ALPACA_SECRET_KEY=<live secret>
ALPACA_BASE_URL=https://api.alpaca.markets
```

The bot will log `Account equity: $xxxx.xx (LIVE)` at startup. If you ever see `LIVE` and didn't expect it, kill the process immediately.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ALPACA_API_KEY and ALPACA_SECRET_KEY must be set` | Forgot `source .env` | Run `set -a && source .env && set +a` in the current shell |
| `subscription does not permit querying recent SIP data` | Free-tier Alpaca + SIP feed | Set `ALPACA_DATA_FEED=iex` in `.env` (default) |
| `ModelTamperingError: hash mismatch` | `models/classifier.pkl` was regenerated but `.sha256` is stale (or vice versa) | Retrain — both files are written atomically together |
| `FeatureError: Insufficient data — got 45 rows, need 97` | `--days` too small or ticker has a short history | Bump to `--days 756` |
| AUC-ROC below 0.55 on retrain | Model degradation or data issue | Try more history (`--days 1008`), more tickers, or investigate a specific ticker's OHLCV |
| Dashboard shows "no trades" but `main.py` logged "Submitted order" | Supabase service role key wrong, or migrations not applied | Check `SUPABASE_SERVICE_ROLE_KEY` matches your project, and that both migrations ran |
| `main.py` exits 0 with `ml_dead_zone` every morning | ML probability is close to 0.50 across all regimes — model is uncertain | This is correct behavior. Retrain with more data or accept that not every day has a trade |
| Kill switch fires but you don't know why | `risk_engine.check_kill_switch()` hit the 2% daily loss threshold | Check Alpaca P&L and `pipeline_runs` table — nothing you can do until next trading day |

---

## 11. Recurring tasks

| When | What |
|---|---|
| Every morning | `python main.py --ticker SPY` (or via cron) |
| Weekly | Glance at the dashboard — confirm gates are firing sensibly |
| Monthly | `pip list --outdated`; if anything security-critical, regenerate `requirements.txt` with `pip-compile --generate-hashes` |
| Quarterly | Retrain the model (`train_model.py`), commit new `classifier.sha256` |
| On Alpaca key rotation | Update `.env`, restart any running processes |
| On repo pull | Re-run `pytest backend/tests/` before the next live run |

---

## 12. Where things live

| Path | What it is |
|---|---|
| `main.py` | Daily entrypoint |
| `backend/core/morning_pipeline.py` | Orchestrates ML → MC → Claude review |
| `backend/core/risk_engine.py` | Hardcoded risk constants + kill switch |
| `backend/core/feature_engineering.py` | 12-feature computation (Hurst, OU, regime) |
| `backend/core/model.py` | SHA-256-verified XGBoost loader + `predict_direction` |
| `backend/core/order_executor.py` | Alpaca bracket order submission |
| `backend/core/journal.py` | Supabase trade logging |
| `backend/scripts/train_model.py` | Retraining script |
| `backend/tests/` | 139 tests — run before every live deploy |
| `supabase/migrations/` | DB schema |
| `dashboard/` | Next.js read-only UI |
| `models/classifier.sha256` | Committed — integrity anchor |
| `models/classifier.pkl` | **Not** committed — regenerate locally |

See also: [README.md](README.md), [BRAIN.md](BRAIN.md), [OVERVIEW.txt](OVERVIEW.txt), [SECURITY.md](SECURITY.md).
