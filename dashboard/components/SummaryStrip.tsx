// Server Component — no 'use client' needed

interface SummaryStripProps {
  equity: string        // raw string from ALPACA_PAPER_EQUITY env var
  dailyPnl: number     // sum of pnl_pct for today's closed trades
  tradesCount: number  // total trades today (open + closed)
  openCount: number    // number of currently open trades
  halted: boolean      // true when dailyPnl <= -0.02
}

function fmtPnl(pnl: number): string {
  const sign = pnl >= 0 ? '+' : ''
  return `${sign}${(pnl * 100).toFixed(2)}%`
}

function fmtEquity(raw: string): string {
  const n = parseFloat(raw)
  if (isNaN(n)) return raw
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/**
 * Four summary metric cards: Equity, Daily P&L, Trades Today, System Status.
 */
export default function SummaryStrip({
  equity,
  dailyPnl,
  tradesCount,
  openCount,
  halted,
}: SummaryStripProps) {
  return (
    <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
      {/* Equity */}
      <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
        <p className="mb-1 text-xs uppercase tracking-wider text-gray-500">Equity</p>
        <p className="font-mono text-2xl font-bold text-gray-100">{fmtEquity(equity)}</p>
        <p className="mt-1 text-xs text-gray-600">paper account</p>
      </div>

      {/* Daily P&L */}
      <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
        <p className="mb-1 text-xs uppercase tracking-wider text-gray-500">Daily P&L</p>
        <p
          className={`font-mono text-2xl font-bold ${
            dailyPnl >= 0 ? 'text-emerald-400' : 'text-red-400'
          }`}
        >
          {fmtPnl(dailyPnl)}
        </p>
        <p className="mt-1 text-xs text-gray-600">limit: −2.00%</p>
      </div>

      {/* Trades Today */}
      <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
        <p className="mb-1 text-xs uppercase tracking-wider text-gray-500">Trades Today</p>
        <p className="font-mono text-2xl font-bold text-gray-100">
          {tradesCount}
          <span className="text-lg text-gray-600"> / 3</span>
        </p>
        <p className="mt-1 text-xs text-gray-600">{openCount} open</p>
      </div>

      {/* Status */}
      <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
        <p className="mb-1 text-xs uppercase tracking-wider text-gray-500">Status</p>
        {halted ? (
          <p className="font-mono text-2xl font-bold text-red-400">HALTED</p>
        ) : (
          <p className="font-mono text-2xl font-bold text-emerald-400">ACTIVE</p>
        )}
        <p className="mt-1 text-xs text-gray-600">
          kill switch: {halted ? 'triggered' : 'armed'}
        </p>
      </div>
    </div>
  )
}
