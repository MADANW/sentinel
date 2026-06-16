-- ============================================================
-- Migration 003: Create ea_backtests table
-- Stores MT5 Strategy Tester single-run backtest summaries.
-- Run in Supabase SQL editor or via supabase db push.
-- ============================================================

CREATE TABLE IF NOT EXISTS ea_backtests (
  id                    UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  imported_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

  -- Identity
  ea_name               TEXT          NOT NULL,   -- e.g. "bb_scalper", "MACrossoverEA"
  source_file           TEXT          NOT NULL,   -- original filename, e.g. "bb-USDCAD.xlsx"
  symbol                TEXT          NOT NULL,   -- e.g. "USDJPY", "USDCAD"
  period                TEXT,                     -- e.g. "M5 (2026.01.01 - 2026.03.26)"

  -- Account settings
  initial_deposit       NUMERIC(14,2),
  currency              TEXT,
  leverage              TEXT,                     -- e.g. "1:100"

  -- Core results
  total_net_profit      NUMERIC(14,2),
  gross_profit          NUMERIC(14,2),
  gross_loss            NUMERIC(14,2),
  profit_factor         NUMERIC(10,6),
  recovery_factor       NUMERIC(10,6),
  sharpe_ratio          NUMERIC(10,6),
  expected_payoff       NUMERIC(10,4),
  equity_dd_pct         NUMERIC(8,4),             -- max equity drawdown %

  -- Trade counts
  total_trades          INTEGER,
  profit_trades         INTEGER,
  loss_trades           INTEGER,

  -- EA parameters stored as JSONB (flexible — each EA has different params)
  ea_params             JSONB         NOT NULL DEFAULT '{}'
);

-- Query by EA and symbol
CREATE INDEX IF NOT EXISTS ea_backtests_ea_name_idx  ON ea_backtests (ea_name);
CREATE INDEX IF NOT EXISTS ea_backtests_symbol_idx   ON ea_backtests (symbol);
CREATE INDEX IF NOT EXISTS ea_backtests_imported_idx ON ea_backtests (imported_at DESC);
