# Scheduling — Remote Linux (systemd)

Run `main.py` weekdays at 09:45 America/New_York on a remote Ubuntu box via systemd user timer.

---

## Target Environment

- Host: remote Ubuntu, SSH key auth working
- User: `madanw` (non-root)
- Repo path: `/home/madanw/projects/algo-bot`
- Venv: `/home/madanw/projects/algo-bot/.venv`
- Python: 3.11+
- Box timezone: `America/Chicago` (CDT) — but unit pins fire time to `America/New_York` so it tracks the market regardless of box TZ
- Fire time: weekdays 09:45 ET (15 min after open)

---

## Pre-flight Checklist

Run on the remote box (`ssh madanw@<host>`):

1. Clone repo to `~/projects/algo-bot` (skip if already cloned).
2. Create venv + install deps:
   ```bash
   cd ~/projects/algo-bot
   python3 -m venv .venv
   .venv/bin/pip install --require-hashes -r backend/requirements.txt
   ```
3. Drop `.env` with secrets (paper keys, Supabase, Anthropic).
   - Format: `KEY=VALUE` per line, no quotes, no `export`.
   - `chmod 600 .env`
4. Train model once:
   ```bash
   .venv/bin/python -m backend.scripts.train_model --tickers SPY,QQQ,IWM --days 756
   ```
   Confirm `models/classifier.pkl` + `models/classifier.sha256` exist.
5. Smoke test the pipeline:
   ```bash
   .venv/bin/python main.py --tickers SPY,QQQ,IWM
   ```
   Confirm clean exit and a row appears in Supabase `pipeline_runs`.

---

## systemd Unit Files

Create the user unit dir:

```bash
mkdir -p ~/.config/systemd/user
```

### `~/.config/systemd/user/algo-bot.service`

```ini
[Unit]
Description=Algo-bot morning pipeline (one-shot)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/home/madanw/projects/algo-bot
EnvironmentFile=/home/madanw/projects/algo-bot/.env
ExecStart=/home/madanw/projects/algo-bot/.venv/bin/python main.py --tickers SPY,QQQ,IWM
StandardOutput=append:/home/madanw/algo-bot.log
StandardError=append:/home/madanw/algo-bot.log
TimeoutStartSec=300

[Install]
WantedBy=default.target
```

### `~/.config/systemd/user/algo-bot.timer`

```ini
[Unit]
Description=Fire algo-bot weekdays 09:45 America/New_York

[Timer]
OnCalendar=Mon..Fri 09:45 America/New_York
Persistent=false
Unit=algo-bot.service

[Install]
WantedBy=timers.target
```

---

## Enable + Verify

```bash
systemctl --user daemon-reload
systemctl --user enable --now algo-bot.timer
loginctl enable-linger madanw                # survive logout / reboot
systemctl --user list-timers | grep algo
```

`list-timers` should print the next fire time.

### Logs

```bash
tail -f ~/algo-bot.log
journalctl --user -u algo-bot.service -f
```

### Manual Trigger (end-to-end test)

```bash
systemctl --user start algo-bot.service
systemctl --user status algo-bot.service
```

Then check Supabase `pipeline_runs` for the new row.

---

## Caveats

- `EnvironmentFile` parses `KEY=VALUE` only. No quotes, no `export`. Strip them if present.
- Venv path assumed `.venv`. Adjust both unit files if you used a different name.
- `loginctl enable-linger madanw` is **required** — without it, the user manager exits on logout and the timer dies with it.
- Market holidays are not handled at scheduler level. The bot will fire on Thanksgiving etc., but `morning_pipeline` detects a closed market and exits clean (no trade, logged with `skip_reason`).
- Pinning `OnCalendar` to `America/New_York` handles DST automatically — no need to edit the unit twice a year.

---

## Symptom → Check Triage

| Symptom | First check |
|---------|-------------|
| No `pipeline_runs` row today | `systemctl --user list-timers` (timer enabled?), `~/algo-bot.log` (did it fire?) |
| Log shows fire, no DB row | Supabase creds in `.env`, `pipeline_logger` errors in log |
| DB row but `trade_submitted=false` | Read `skip_reason` column |
| Unexpected trade | Alpaca dashboard → Orders tab is ground truth |
| Timer dies after reboot | `loginctl show-user madanw \| grep Linger` should be `Linger=yes` |
