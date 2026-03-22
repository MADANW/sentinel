# Security Policy

## Reporting a Vulnerability

If you find a security issue in this repository, **do not open a public GitHub issue.**

Email the maintainer directly or open a [GitHub private security advisory](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability).

---

## Threat Model

This is a public repo containing a personal automated trading system. The key threats are:

| Threat | Mitigation |
|--------|-----------|
| Secrets committed to git | `.gitignore`, gitleaks pre-commit + CI, `.env` never committed |
| Prompt injection via news headlines | `bias_validator.py` sanitizes all Claude output before it touches trade logic |
| Risk engine bypass | Constants are hardcoded, not configurable. Kill switch exits the process. |
| Runaway orders | Hard cap: 3 trades/day, 2% daily loss limit, process exits on breach |
| Leaked API keys | Minimal Alpaca permissions, keys stored only in `.env` / VPS environment |
| Dependency vulnerabilities | Pinned `requirements.txt`, `pip-audit` + `npm audit` in CI |
| Unauthorized live trading | `TRADING_ENV=live` must be explicitly set; defaults to paper |

---

## Environment Separation

| Variable | Paper (safe default) | Live (real money) |
|----------|---------------------|-------------------|
| `TRADING_ENV` | `paper` | `live` |
| `ALPACA_BASE_URL` | `https://paper-api.alpaca.markets` | `https://api.alpaca.markets` |
| Alpaca keys | Paper API keys | Live API keys |

**Never set `TRADING_ENV=live` on a machine that also has `paper` keys.**

---

## Secret Rotation Runbook

### If any secret is leaked (committed to git, logged, or exposed):

1. **Immediately revoke** the key in the provider dashboard:
   - Alpaca: https://alpaca.markets → Account → API Keys → Delete
   - Anthropic: https://console.anthropic.com → API Keys → Revoke
   - Supabase: https://supabase.com → Project Settings → API → Regenerate

2. **Generate new keys** and update:
   - Your `.env` on the VPS
   - GitHub Actions secrets (Settings → Secrets and variables → Actions)

3. **Purge from git history** if committed:
   ```bash
   # Install BFG Repo Cleaner
   brew install bfg   # or download from https://rtyley.github.io/bfg-repo-cleaner/

   # Remove the file containing the secret from all history
   bfg --delete-files .env

   # Or replace a specific string
   echo "OLD_SECRET_VALUE" > secrets.txt
   bfg --replace-text secrets.txt

   git reflog expire --expire=now --all
   git gc --prune=now --aggressive
   git push --force
   ```

4. **Notify** anyone with access to the repo that they should re-clone.

5. **Review** Alpaca order history and Anthropic API usage logs for unauthorized activity.

---

## Alpaca API Permission Scoping

When creating Alpaca API keys, set the minimum required permissions:

| Permission | Required | Notes |
|-----------|----------|-------|
| Trading | Yes | Submit, cancel orders |
| Account info | Yes | Read equity for position sizing |
| Market data | Yes | Price feeds |
| Account funding | **No** | Never grant this |
| Crypto | **No** | Equity only |

Use **paper keys** for all development and testing.

---

## VPS Hardening Checklist

When deploying to a VPS:

- [ ] Disable password SSH login — use key-only auth
- [ ] Change default SSH port (optional but reduces noise)
- [ ] Enable `ufw` firewall: allow only SSH + your dashboard port
- [ ] Run the bot as a non-root user with a dedicated service account
- [ ] Store secrets in environment variables, not files: `export VAR=value` in `/etc/environment` or a systemd unit file with `EnvironmentFile`
- [ ] Set file permissions: `chmod 600 .env` if using a file
- [ ] Enable automatic security updates: `unattended-upgrades`
- [ ] Monitor with `fail2ban` to block brute-force SSH attempts

---

## Risk Engine Invariants

The following are hardcoded in `backend/core/risk_engine.py` and **cannot be overridden at runtime**:

| Limit | Value | Consequence of breach |
|-------|-------|-----------------------|
| Risk per trade | 1% of equity | Order rejected |
| Daily loss limit | 2% of equity | Process exits (kill switch) |
| Max trades/day | 3 | Order rejected |
| Min Claude confidence | 60% | Signal ignored, no trade |

Any change to these values requires a code change, code review, and a new deployment.
