# 🧠 Project Brain — algo-bot

> Hybrid AI trading bot where you set the directional bias and the bot handles execution, sizing, and risk.

---

## Project Overview

algo-bot is a personal trading system built for a single operator. You provide a morning directional call (bullish/bearish/neutral); the bot queries Claude API for a confidence-weighted bias, validates it through a strict sanitizer, and executes bracket orders via Alpaca. A hardcoded risk engine enforces 1% risk/trade, a 2% daily loss kill switch, and a 3-trade daily cap — none of these limits are configurable at runtime.

---

## Project Status

- **Current Phase:** Active Development
- **Last Updated:** 2026-04-16
- **Active Branch:** claude/update-ml-upgrade-plan-Ayo47

---

## Deliverables & Sprints

### Sprint 1 — Security Foundation

- [x] Hardcoded risk engine with kill switch (`backend/core/risk_engine.py`)
- [x] Claude API response validator with injection protection (`backend/core/bias_validator.py`)
- [x] Full test suite for risk engine and bias validator (22 tests)
- [x] CI pipeline: secret scan (gitleaks), pip-audit, bandit, pytest
- [x] Pre-commit hooks (secret scanning)
- [x] `.env.example` + `.gitignore` (secrets never committed)
- [x] Threat model and incident response runbook (`SECURITY.md`)
- [x] BRAIN.md (this file)
- [ ] Fix CI: `No module named 'backend'` (missing `pythonpath` config)
- [ ] Fix CI: `pandas-ta` version incompatible with `pandas>=2.0`

### Sprint 2 — Core Trading Logic

- [x] EMA crossover signal detection (`backend/core/signals.py`)
- [x] Morning bias prompt pipeline — Claude API call → validate → decide (`backend/core/morning_pipeline.py`)
- [x] Alpaca bracket order submission (`backend/core/order_executor.py`)
- [x] Paper trading end-to-end smoke test (`backend/tests/test_smoke.py`)

### Sprint 3 — Dashboard

- [x] Next.js 15 app scaffold with Tailwind (`dashboard/`)
- [x] Supabase trades table schema (`supabase/migrations/001_create_trades.sql`)
- [x] Python trade journal — log/close/query trades (`backend/core/journal.py`)
- [x] Daily P&L display with trade table (`dashboard/app/page.tsx`)

### Sprint F — Mathematical Foundation Upgrade

- [x] Added regime features to `FeatureRow` and `FEATURE_COLUMNS`: `hurst_exponent`, `ou_log_half_life`, `ou_zscore`, `regime_label`
- [x] Multi-scale R/S Hurst estimator (`_compute_hurst`, 96-bar window) + OLS OU fit with saturation fallback (`_compute_ou_features`, 64-bar window) in `feature_engineering.py`
- [x] `_MIN_ROWS` bumped to 97 (96-bar Hurst + one prior close)
- [x] `train_model.py` accepts `--tickers SPY,QQQ,IWM`; per-ticker chronological split then concatenation (no cross-ticker lookahead)
- [x] Free-tier Alpaca support: `ALPACA_DATA_FEED=iex` default in `fetch_ohlcv` (override to `sip` for paid plans)
- [x] 17 new unit tests (Hurst, OU, regime) — full suite: 139 passing
- [x] Classifier retrained on SPY+QQQ+IWM, 756 days → AUC-ROC 0.5623 (above 0.55 gate)

### Sprint E — Paper Trading Loop

- [x] `backend/scripts/monitor.py` — polls Alpaca closed orders every 60s; reconciles with open journal trades; calls `close_trade()` on fills; SIGTERM-safe; `--once` flag for testing
- [x] `systemd/algo-bot.service` + `systemd/algo-bot.timer` — committed unit files (copy to `~/.config/systemd/user/`; see SCHEDULING.md)
- [x] `systemd/algo-bot-monitor.service` — long-running monitor unit

Deferred: CODEOWNERS.

### Sprint G — MT5 Integration (Layer 1 + 2)

- [x] Created `mql5/experts/` — BB Scalper (`bb_scalper.mq5`, `bb_scalper-LS1.mq5`) and MA Crossover (`MACrossoverEA.mq5`) moved in from EA-tests project
- [x] Created `mql5/backtests/` — 12 backtest files moved in (6 single-run reports, 3 optimization result sets)
- [x] Supabase migration `003_create_ea_backtests.sql` — `ea_backtests` table with JSONB `ea_params` for flexible per-EA parameters
- [x] `backend/scripts/import_backtests.py` — parse single-run MT5 report xlsx → Supabase insert; auto-skips SpreadsheetML optimization exports

Layer 3 (bias bridge) complete:
- [x] `backend/core/bias_writer.py` — writes `{direction, confidence, reasoning, timestamp}` JSON atomically after each pipeline run
- [x] `mql5/include/AlgoBotBias.mqh` — shared MQL5 include; `ReadAlgoBias()` reads + validates file age, returns false if absent/stale/neutral
- [x] Both EAs updated (v4.01 / v2.01) — bias gate added before entry; only trades in bias-confirmed direction
- [x] `main.py` calls `write_bias_file()` after `run_morning_pipeline` (non-fatal on OSError)

Setup: copy `algo-bot-bias.json` from `/tmp/` into MT5 Common Files folder (`%APPDATA%\MetaQuotes\Terminal\Common\Files\`), or set `BIAS_FILE_PATH` to write directly there.

---

## Directory Structure

```
algo-bot/
├── backend/
│   ├── core/
│   │   ├── risk_engine.py      — Hardcoded risk constants + kill switch
│   │   └── bias_validator.py   — Claude API response sanitizer
│   ├── tests/
│   │   └── test_risk_engine.py — Risk engine + bias validator tests (22 tests)
│   └── requirements.txt        — Pinned Python dependencies
├── mql5/
│   ├── experts/                — MQL5 EA source files (.mq5)
│   └── backtests/              — MT5 Strategy Tester reports (.xlsx)
├── dashboard/                  — Next.js app (Sprint 3)
├── .env.example                — Safe secrets template (copy to .env, never commit .env)
├── .gitignore                  — Ensures secrets stay out of git
├── .pre-commit-config.yaml     — Secret scanning + linting on every commit
├── pyproject.toml              — Bandit, ruff, pytest config
├── SECURITY.md                 — Threat model, secret rotation, VPN hardening
├── BRAIN.md                    — This file
└── .github/workflows/
    └── security.yml            — CI: gitleaks, pip-audit, bandit, pytest
```

---

## Mistakes & Fixes

| # | What Went Wrong | Root Cause | Fix Applied | Date |
|---|-----------------|------------|-------------|------|
| 1 | `No module named 'backend'` in all 22 tests | `pythonpath` not set in pytest config; project root not on `sys.path` | Added `pythonpath = ["."]` to `[tool.pytest.ini_options]` in `pyproject.toml` | 2026-03-22 |
| 2 | `pip-audit` CI fails: `pandas-ta==0.3.14b0` not resolvable | `pandas-ta` pre-release is incompatible with `pandas>=2.0`; package effectively unmaintained | Removed `pandas-ta`; implemented EMA crossover directly using `pandas.ewm()` in Sprint 2 — no external TA library needed | 2026-03-22 |
| 3 | `crossover_detected is False` assertion fails in test | `pandas` comparisons return `numpy.bool_`, not Python `bool`; `numpy.bool_(False) is False` → `False` | Added explicit `bool()` cast in `signals.py` crossover detection | 2026-03-23 |

---

## Claude Rules

### Global Defaults (always apply)

- **No vibe coding.** Every line of code must be intentional and understood. Do not generate code you cannot explain.
- **Code rigidity first.** Prefer explicit, strict, and typed code over flexible or implicit patterns. Avoid loose equality, dynamic typing abuse, or ambiguous logic.
- **Security by default.** Never expose secrets, API keys, or sensitive data. Always validate and sanitize inputs. Prefer allowlists over denylists. Flag any pattern that could introduce a vulnerability.
- **No silent failures.** All errors must be caught, logged, or surfaced. Never swallow exceptions silently.
- **Minimal surface area.** Don't add dependencies, files, or abstractions unless necessary. Prefer built-ins and existing patterns in the codebase.
- **Explain every change.** Before making a non-trivial change, state what you're doing and why. After making it, confirm what changed.
- **Match existing style.** Follow the conventions already in the codebase — naming, formatting, file structure — unless told otherwise.
- **Ask before deleting.** Never remove a file, function, or block of code without explicit confirmation.

### Project-Specific Rules

- **Risk constants are immutable.** `MAX_RISK_PER_TRADE`, `MAX_DAILY_LOSS`, `MAX_TRADES_PER_DAY`, `MIN_CONFIDENCE_TO_TRADE` live in `risk_engine.py` and must never be moved to config files, env vars, or function parameters.
- **Claude output is untrusted input.** All Claude API responses must pass through `bias_validator.parse_bias_response()` (legacy) or `bias_validator.parse_claude_review()` (ML pipeline) before influencing any trading decision. Never use raw LLM output directly.
- **Paper trading by default.** `TRADING_ENV=paper` is the safe default. Live trading requires an explicit `TRADING_ENV=live` env var set intentionally.
- **Test the risk engine first.** Any PR that touches `risk_engine.py` must include updated tests and must not reduce test coverage.

---

## Pickle Security Note

`models/classifier.pkl` is a Python pickle file generated by `backend/scripts/train_model.py`.

**CRITICAL: Never accept `classifier.pkl` from an external source.** Pickle files can execute arbitrary code on deserialization. The file must only be generated by `train_model.py` on a trusted machine.

**Integrity protection:** `train_model.py` writes a SHA-256 hash to `models/classifier.sha256` at generation time. `model.py` verifies this hash against the pkl bytes BEFORE calling `pickle.load()`. If the hash does not match, the process immediately raises `ModelTamperingError` and no model is loaded.

**Git policy:**
- `models/classifier.pkl` is **gitignored** (binary artifact — do not commit).
- `models/classifier.sha256` **must be committed** — it serves as the integrity anchor.

**Regeneration:** After retraining, regenerate both files together using `train_model.py`. The atomic write pattern (`.tmp` → `os.replace`) ensures the pkl and sha256 are always in sync.
