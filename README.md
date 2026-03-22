# algo-bot

Hybrid AI trading system. You make the directional call; the bot handles execution, sizing, and risk.

## Stack

- **Claude API** → morning bias (bullish/bearish/neutral + confidence)
- **Python backend** → EMA crossover detection + Alpaca bracket orders
- **Next.js dashboard** → trade journal + Supabase
- **Hardcoded risk engine** → 1% risk/trade, 2% daily loss kill switch, 3 trades/day max

## Quick Start

```bash
# 1. Clone and set up secrets
cp .env.example .env
# Fill in .env — never commit it

# 2. Install Python dependencies
cd backend
pip install -r requirements.txt

# 3. Install pre-commit hooks (runs secret scanning before every commit)
pip install pre-commit
pre-commit install

# 4. Run tests
pytest

# 5. Start the bot (paper trading by default)
python main.py
```

## Project Structure

```
algo-bot/
├── backend/
│   ├── core/
│   │   ├── risk_engine.py      # Immutable risk constants + kill switch
│   │   └── bias_validator.py   # Claude API response sanitizer
│   ├── tests/
│   │   └── test_risk_engine.py
│   └── requirements.txt        # Pinned dependencies
├── dashboard/                  # Next.js app (future)
├── .env.example                # Safe template — copy to .env
├── .gitignore                  # Secrets never committed
├── .pre-commit-config.yaml     # Secret scanning + linting hooks
├── pyproject.toml
├── SECURITY.md                 # Threat model + incident response
└── .github/workflows/
    └── security.yml            # CI: secret scan, pip-audit, bandit, tests
```

## Security

See [SECURITY.md](SECURITY.md) for the full threat model, secret rotation runbook, and VPS hardening checklist.

**TL;DR:**
- Secrets live in `.env` only — never in code or git history
- `TRADING_ENV=paper` by default — must explicitly set `=live` for real money
- Risk limits are hardcoded and cannot be overridden at runtime
- Pre-commit hooks and CI scan every commit for secrets

## Risk Limits (hardcoded, not configurable)

| Limit | Value |
|-------|-------|
| Risk per trade | 1% of equity |
| Daily loss kill switch | 2% of equity |
| Max trades per day | 3 |
| Min Claude confidence to trade | 60% |
