import { createServerClient } from '@/lib/supabase'

// Always fetch fresh data — no caching for a live trading dashboard
export const revalidate = 0

interface Trade {
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

async function getTodaysTrades(): Promise<Trade[]> {
  const supabase = createServerClient()
  const today = new Date().toISOString().split('T')[0]

  const { data, error } = await supabase
    .from('trades')
    .select('*')
    .gte('created_at', today)
    .order('created_at', { ascending: false })

  if (error) {
    console.error('Failed to fetch trades:', error.message)
    return []
  }
  return data ?? []
}

function fmt(n: number, decimals = 2) {
  return n.toFixed(decimals)
}

function fmtPnl(pnl: number) {
  const sign = pnl >= 0 ? '+' : ''
  return `${sign}${fmt(pnl * 100)}%`
}

function fmtTime(iso: string) {
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'America/New_York',
  })
}

export default async function Dashboard() {
  const trades = await getTodaysTrades()

  const closedTrades = trades.filter((t) => t.status === 'closed')
  const dailyPnl = closedTrades.reduce((sum, t) => sum + (t.pnl_pct ?? 0), 0)
  const openCount = trades.filter((t) => t.status === 'open').length
  const killSwitch = dailyPnl <= -0.02

  return (
    <main className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto">

        {/* Header */}
        <div className="flex items-baseline justify-between mb-8">
          <h1 className="text-2xl font-mono font-bold tracking-tight">algo-bot</h1>
          <span className="text-sm text-gray-500 font-mono">
            {new Date().toLocaleDateString('en-US', {
              weekday: 'long',
              year: 'numeric',
              month: 'long',
              day: 'numeric',
              timeZone: 'America/New_York',
            })}
          </span>
        </div>

        {/* Kill switch banner */}
        {killSwitch && (
          <div className="mb-6 rounded-lg border border-red-800 bg-red-950/50 px-5 py-4">
            <p className="text-red-400 font-mono font-semibold text-sm">
              ⛔ KILL SWITCH TRIGGERED — daily loss limit reached. No further orders today.
            </p>
          </div>
        )}

        {/* Summary cards */}
        <div className="grid grid-cols-3 gap-4 mb-8">
          <div className="bg-gray-900 rounded-lg p-5 border border-gray-800">
            <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">Daily P&L</p>
            <p className={`text-3xl font-mono font-bold ${dailyPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {fmtPnl(dailyPnl)}
            </p>
            <p className="text-xs text-gray-600 mt-1">limit: −2.00%</p>
          </div>

          <div className="bg-gray-900 rounded-lg p-5 border border-gray-800">
            <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">Trades Today</p>
            <p className="text-3xl font-mono font-bold">
              {trades.length}
              <span className="text-lg text-gray-600"> / 3</span>
            </p>
            <p className="text-xs text-gray-600 mt-1">{openCount} open</p>
          </div>

          <div className="bg-gray-900 rounded-lg p-5 border border-gray-800">
            <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">Status</p>
            {killSwitch ? (
              <p className="text-3xl font-mono font-bold text-red-500">HALTED</p>
            ) : (
              <p className="text-3xl font-mono font-bold text-emerald-400">ACTIVE</p>
            )}
            <p className="text-xs text-gray-600 mt-1">
              kill switch: {killSwitch ? 'triggered' : 'armed'}
            </p>
          </div>
        </div>

        {/* Trade table */}
        <div className="bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
          <div className="px-5 py-4 border-b border-gray-800">
            <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
              Today&apos;s Trades
            </h2>
          </div>

          {trades.length === 0 ? (
            <p className="text-center text-gray-600 py-12 font-mono text-sm">
              No trades today.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-gray-500 uppercase border-b border-gray-800">
                    <th className="px-5 py-3 text-left">Time</th>
                    <th className="px-5 py-3 text-left">Symbol</th>
                    <th className="px-5 py-3 text-left">Direction</th>
                    <th className="px-5 py-3 text-right">Qty</th>
                    <th className="px-5 py-3 text-right">Entry</th>
                    <th className="px-5 py-3 text-right">Stop</th>
                    <th className="px-5 py-3 text-right">Target</th>
                    <th className="px-5 py-3 text-right">P&L</th>
                    <th className="px-5 py-3 text-right">Confidence</th>
                    <th className="px-5 py-3 text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800">
                  {trades.map((trade) => (
                    <tr key={trade.id} className="hover:bg-gray-800/40 transition-colors">
                      <td className="px-5 py-3 font-mono text-gray-400 text-xs">
                        {fmtTime(trade.created_at)}
                      </td>
                      <td className="px-5 py-3 font-mono font-semibold">
                        {trade.symbol}
                      </td>
                      <td className="px-5 py-3">
                        <span
                          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
                            trade.direction === 'bullish'
                              ? 'bg-emerald-900/50 text-emerald-400'
                              : 'bg-red-900/50 text-red-400'
                          }`}
                        >
                          {trade.direction === 'bullish' ? '↑' : '↓'} {trade.direction}
                        </span>
                      </td>
                      <td className="px-5 py-3 font-mono text-right">{trade.qty}</td>
                      <td className="px-5 py-3 font-mono text-right">
                        ${fmt(trade.entry_price)}
                      </td>
                      <td className="px-5 py-3 font-mono text-right text-red-400">
                        ${fmt(trade.stop_price)}
                      </td>
                      <td className="px-5 py-3 font-mono text-right text-emerald-400">
                        ${fmt(trade.take_profit_price)}
                      </td>
                      <td className="px-5 py-3 font-mono text-right">
                        {trade.pnl_pct !== null ? (
                          <span className={trade.pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                            {fmtPnl(trade.pnl_pct)}
                          </span>
                        ) : (
                          <span className="text-gray-600">—</span>
                        )}
                      </td>
                      <td className="px-5 py-3 font-mono text-right text-gray-400">
                        {trade.bias_confidence !== null
                          ? `${fmt(trade.bias_confidence * 100, 0)}%`
                          : '—'}
                      </td>
                      <td className="px-5 py-3 text-center">
                        <span
                          className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                            trade.status === 'open'
                              ? 'bg-blue-900/50 text-blue-400'
                              : trade.status === 'closed'
                              ? 'bg-gray-800 text-gray-400'
                              : 'bg-yellow-900/50 text-yellow-400'
                          }`}
                        >
                          {trade.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </main>
  )
}
