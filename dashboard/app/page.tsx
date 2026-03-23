import { createServerClient } from '@/lib/supabase'
import { isMarketOpen } from '@/lib/market'
import type { Trade, PipelineRun } from '@/lib/types'
import Header from '@/components/Header'
import KillSwitchBanner from '@/components/KillSwitchBanner'
import SummaryStrip from '@/components/SummaryStrip'
import PipelineGates from '@/components/PipelineGates'
import TradeTable from '@/components/TradeTable'

// Always fetch fresh data — no caching for a live trading dashboard
export const revalidate = 0

async function getTrades(): Promise<Trade[]> {
  const supabase = createServerClient()
  const today = new Date().toISOString().split('T')[0]

  const { data, error } = await supabase
    .from('trades')
    .select('*')
    .gte('created_at', today)
    .order('created_at', { ascending: false })
    .limit(50)

  if (error) {
    console.error('Failed to fetch trades:', error.message)
    return []
  }
  return (data ?? []) as Trade[]
}

async function getLatestPipelineRun(): Promise<PipelineRun | null> {
  const supabase = createServerClient()
  const today = new Date().toISOString().split('T')[0]

  const { data, error } = await supabase
    .from('pipeline_runs')
    .select('*')
    .gte('created_at', today)
    .order('created_at', { ascending: false })
    .limit(1)
    .maybeSingle()

  if (error) {
    console.error('Failed to fetch pipeline run:', error.message)
    return null
  }
  return data as PipelineRun | null
}

export default async function Dashboard() {
  // Parallel server-side fetches
  const [trades, latestRun] = await Promise.all([
    getTrades(),
    getLatestPipelineRun(),
  ])

  // Derived values
  const closedTrades = trades.filter((t) => t.status === 'closed')
  const dailyPnl = closedTrades.reduce((sum, t) => sum + (t.pnl_pct ?? 0), 0)
  const openCount = trades.filter((t) => t.status === 'open').length
  const halted = dailyPnl <= -0.02
  const marketOpen = isMarketOpen()

  // Env vars — read server-side only; never exposed to browser
  const accountNumber = process.env.ALPACA_ACCOUNT_NUMBER ?? '—'
  const tradingEnv = process.env.TRADING_ENV ?? 'paper'
  const equity = process.env.ALPACA_PAPER_EQUITY ?? '—'

  return (
    <main className="min-h-screen p-6">
      <div className="mx-auto max-w-7xl">
        <KillSwitchBanner active={halted} />
        <Header
          accountNumber={accountNumber}
          tradingEnv={tradingEnv}
          marketOpen={marketOpen}
        />
        <SummaryStrip
          equity={equity}
          dailyPnl={dailyPnl}
          tradesCount={trades.length}
          openCount={openCount}
          halted={halted}
        />
        <PipelineGates run={latestRun} />
        <TradeTable trades={trades} />
      </div>
    </main>
  )
}
