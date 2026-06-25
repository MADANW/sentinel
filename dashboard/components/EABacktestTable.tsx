import type { EABacktest } from '@/lib/types'

interface EABacktestTableProps {
  backtests: EABacktest[]
}

function fmtProfit(n: number | null): string {
  if (n == null) return '—'
  const sign = n >= 0 ? '+' : ''
  return `${sign}$${n.toFixed(2)}`
}

function fmtFactor(n: number | null): string {
  if (n == null) return '—'
  return n.toFixed(3)
}

function winRate(b: EABacktest): string {
  if (b.total_trades == null || b.profit_trades == null || b.total_trades === 0) return '—'
  return `${((b.profit_trades / b.total_trades) * 100).toFixed(1)}%`
}

export default function EABacktestTable({ backtests }: EABacktestTableProps) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 overflow-hidden">
      <div className="border-b border-gray-800 px-5 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
          MT5 Backtests
          <span className="ml-2 font-mono font-normal normal-case text-gray-600">
            {backtests.length} reports
          </span>
        </h2>
      </div>

      {backtests.length === 0 ? (
        <p className="py-12 text-center font-mono text-sm text-gray-600">
          No backtest data. Run{' '}
          <span className="text-gray-400">python -m backend.scripts.import_backtests mql5/backtests/</span>
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-xs uppercase text-gray-500">
                <th className="px-4 py-2 text-left">EA</th>
                <th className="px-4 py-2 text-left">Symbol</th>
                <th className="px-4 py-2 text-left">Period</th>
                <th className="px-4 py-2 text-right">Net Profit</th>
                <th className="px-4 py-2 text-right">Profit Factor</th>
                <th className="px-4 py-2 text-right">Sharpe</th>
                <th className="px-4 py-2 text-right">DD %</th>
                <th className="px-4 py-2 text-right">Trades</th>
                <th className="px-4 py-2 text-right">Win %</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/60">
              {backtests.map((b) => (
                <tr key={b.id} className="text-gray-300">
                  <td className="px-4 py-2 font-mono font-semibold text-gray-100 text-xs">
                    {b.ea_name}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">{b.symbol}</td>
                  <td className="px-4 py-2 text-xs text-gray-500 max-w-[180px] truncate">
                    {b.period ?? '—'}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs">
                    <span
                      className={
                        b.total_net_profit != null && b.total_net_profit >= 0
                          ? 'text-emerald-400'
                          : 'text-red-400'
                      }
                    >
                      {fmtProfit(b.total_net_profit)}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs">
                    <span
                      className={
                        b.profit_factor != null && b.profit_factor >= 1.5
                          ? 'text-emerald-400'
                          : b.profit_factor != null && b.profit_factor >= 1.0
                          ? 'text-yellow-400'
                          : 'text-red-400'
                      }
                    >
                      {fmtFactor(b.profit_factor)}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-400">
                    {fmtFactor(b.sharpe_ratio)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-red-400">
                    {b.equity_dd_pct != null ? `${b.equity_dd_pct.toFixed(2)}%` : '—'}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-400">
                    {b.total_trades ?? '—'}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-gray-400">
                    {winRate(b)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
