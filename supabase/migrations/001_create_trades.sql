-- ============================================================
-- Migration 001: Create trades table
-- Run this in Supabase SQL editor or via supabase db push
-- ============================================================

CREATE TABLE IF NOT EXISTS trades (
  id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  closed_at          TIMESTAMPTZ,

  -- Order details
  symbol             TEXT        NOT NULL,
  direction          TEXT        NOT NULL CHECK (direction IN ('bullish', 'bearish')),
  qty                INTEGER     NOT NULL CHECK (qty > 0),
  entry_price        NUMERIC(10, 4) NOT NULL,
  stop_price         NUMERIC(10, 4) NOT NULL,
  take_profit_price  NUMERIC(10, 4) NOT NULL,
  fill_price         NUMERIC(10, 4),

  -- Result
  pnl_pct            NUMERIC(8, 6),   -- fraction of equity, e.g. 0.008500 = +0.85%
  status             TEXT        NOT NULL DEFAULT 'open'
                                 CHECK (status IN ('open', 'closed', 'cancelled')),

  -- Provenance
  alpaca_order_id    TEXT,
  bias_confidence    NUMERIC(4, 3),   -- 0.000 – 1.000
  bias_reasoning     TEXT
);

-- Fast daily queries (dashboard + risk engine)
CREATE INDEX IF NOT EXISTS trades_created_at_idx ON trades (created_at DESC);
