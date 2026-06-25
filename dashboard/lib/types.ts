/**
 * types.ts — Supabase row type definitions for sentinel dashboard.
 *
 * All types are derived from the database schema in supabase/migrations/.
 * Use these types for all Supabase query results — never use `any`.
 */

export interface Trade {
  id: string
  created_at: string
  closed_at: string | null

  symbol: string
  direction: 'bullish' | 'bearish'
  qty: number
  entry_price: number
  stop_price: number
  take_profit_price: number
  fill_price: number | null

  pnl_pct: number | null
  status: 'open' | 'closed' | 'cancelled'

  alpaca_order_id: string | null
  bias_confidence: number | null
  bias_reasoning: string | null
}

export interface EABacktest {
  id: string
  imported_at: string

  ea_name: string
  source_file: string
  symbol: string
  period: string | null

  initial_deposit: number | null
  currency: string | null
  leverage: string | null

  total_net_profit: number | null
  gross_profit: number | null
  gross_loss: number | null
  profit_factor: number | null
  recovery_factor: number | null
  sharpe_ratio: number | null
  expected_payoff: number | null
  equity_dd_pct: number | null

  total_trades: number | null
  profit_trades: number | null
  loss_trades: number | null

  ea_params: Record<string, string>
}

export interface PipelineRun {
  id: string
  created_at: string

  ticker: string

  // Gate 1: ML model
  ml_probability: number | null   // 0.0 – 1.0
  ml_signal: 'bullish' | 'bearish' | 'dead_zone' | null

  // Gate 2: Monte Carlo
  mc_hit_rate: number | null      // 0.0 – 1.0
  mc_passed: boolean | null

  // Gate 3: Claude veto
  claude_approved: boolean | null
  claude_reason: string | null

  // Outcome
  trade_submitted: boolean
  skip_reason: string | null
}
