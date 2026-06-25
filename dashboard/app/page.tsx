import { createServerClient } from '@/lib/supabase'
import { isMarketOpen } from '@/lib/market'
import type { Trade, PipelineRun, EABacktest } from '@/lib/types'
import Header from '@/components/Header'
import KillSwitchBanner from '@/components/KillSwitchBanner'
import SummaryStrip from '@/components/SummaryStrip'
import PipelineGates from '@/components/PipelineGates'
import TradeTable from '@/components/TradeTable'
import EABacktestTable from '@/components/EABacktestTable'

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

async function getEABacktests(): Promise<EABacktest[]> {
  const supabase = createServerClient()

  const { data, error } = await supabase
    .from('ea_backtests')
    .select('*')
    .order('imported_at', { ascending: false })
    .limit(100)

  if (error) {
    console.error('Failed to fetch EA backtests:', error.message)
    return []
  }
  return (data ?? []) as EABacktest[]
}

interface PageProps {
  searchParams: Promise<{ tab?: string }>
}

export default async function Dashboard({ searchParams }: PageProps) {
  const { tab } = await searchParams
  const activeTab = tab === 'ea' ? 'ea' : 'trades'

  // Parallel server-side fetches — always fetch all so switching tabs is instant
  const [trades, latestRun, eaBacktests] = await Promise.all([
    getTrades(),
    getLatestPipelineRun(),
    getEABacktests(),
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

        {/* Tab bar */}
        <div className="mb-4 flex gap-1 border-b border-gray-800">
          <a
            href="/"
            className={`px-4 py-2 text-xs font-semibold uppercase tracking-wider transition-colors ${
              activeTab === 'trades'
                ? 'border-b-2 border-blue-500 text-blue-400'
                : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            Trade journal
          </a>
          <a
            href="/?tab=ea"
            className={`px-4 py-2 text-xs font-semibold uppercase tracking-wider transition-colors ${
              activeTab === 'ea'
                ? 'border-b-2 border-blue-500 text-blue-400'
                : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            MT5 backtests
          </a>
        </div>

        {activeTab === 'trades' ? (
          <TradeTable trades={trades} />
        ) : (
          <EABacktestTable backtests={eaBacktests} />
        )}
      </div>
    </main>
  )
}
