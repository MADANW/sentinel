-- ============================================================
-- Migration 002: Create pipeline_runs table
-- Run this in Supabase SQL editor or via supabase db push
-- ============================================================
--
-- One row is inserted per main.py invocation, recording which
-- gates passed/failed and whether a trade was submitted.
-- The dashboard reads the latest row to display gate status.

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Input
  ticker          TEXT        NOT NULL,

  -- Gate 1: ML model
  ml_probability  NUMERIC(5,4),                        -- 0.0000 – 1.0000
  ml_signal       TEXT        CHECK (ml_signal IN ('bullish', 'bearish', 'dead_zone')),

  -- Gate 2: Monte Carlo
  mc_hit_rate     NUMERIC(5,4),                        -- 0.0000 – 1.0000
  mc_passed       BOOLEAN,

  -- Gate 3: Claude veto
  claude_approved BOOLEAN,
  claude_reason   TEXT,                                -- sanitized, truncated 500 chars

  -- Outcome
  trade_submitted BOOLEAN     NOT NULL DEFAULT FALSE,
  skip_reason     TEXT        -- human-readable reason when trade_submitted = FALSE
);

-- Fast descending queries (dashboard reads latest row)
CREATE INDEX IF NOT EXISTS pipeline_runs_created_at_idx ON pipeline_runs (created_at DESC);
