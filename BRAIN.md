# 🧠 Project Brain — algo-bot

> Hybrid AI trading bot where you set the directional bias and the bot handles execution, sizing, and risk.

---

## Project Overview

algo-bot is a personal trading system built for a single operator. You provide a morning directional call (bullish/bearish/neutral); the bot queries Claude API for a confidence-weighted bias, validates it through a strict sanitizer, and executes bracket orders via Alpaca. A hardcoded risk engine enforces 1% risk/trade, a 2% daily loss kill switch, and a 3-trade daily cap — none of these limits are configurable at runtime.

---

## Project Status

- **Current Phase:** Active Development
- **Last Updated:** 2026-03-22
- **Active Branch:** claude/self-learn-0lmtC

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
- **Claude output is untrusted input.** All Claude API responses must pass through `bias_validator.parse_bias_response()` before influencing any trading decision. Never use raw LLM output directly.
- **Paper trading by default.** `TRADING_ENV=paper` is the safe default. Live trading requires an explicit `TRADING_ENV=live` env var set intentionally.
- **Test the risk engine first.** Any PR that touches `risk_engine.py` must include updated tests and must not reduce test coverage.
