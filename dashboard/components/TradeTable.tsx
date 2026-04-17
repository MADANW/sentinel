// Server Component — no 'use client' needed

import type { Trade } from '@/lib/types'
import { formatETTime } from '@/lib/market'

interface TradeTableProps {
  trades: Trade[]
}

function fmtPrice(n: number): string {
  return `$${n.toFixed(2)}`
}

function fmtPnl(pnl: number): string {
  const sign = pnl >= 0 ? '+' : ''
  return `${sign}${(pnl * 100).toFixed(2)}%`
}

function fmtConfidence(c: number): string {
  return `${(c * 100).toFixed(0)}%`
}

/**
 * Trade journal table: last 50 trades ordered newest first.
 * Columns: Time · Symbol · Direction · Qty · Entry · Stop · Target · Fill · P&L · Confidence · Status
 * No hover transitions — density over decoration.
 */
export default function TradeTable({ trades }: TradeTableProps) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 overflow-hidden">
      <div className="border-b border-gray-800 px-5 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
          Trade journal
          <span className="ml-2 font-mono font-normal normal-case text-gray-600">
            last {trades.length} trades
          </span>
        </h2>
      </div>

      {trades.length === 0 ? (
        <p className="py-12 text-center font-mono text-sm text-gray-600">No trades today.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-xs uppercase text-gray-500">
                <th className="px-4 py-2 text-left">Time</th>
                <th className="px-4 py-2 text-left">Symbol</th>
                <th className="px-4 py-2 text-left">Direction</th>
                <th className="px-4 py-2 text-right">Qty</th>
                <th className="px-4 py-2 text-right">Entry</th>
                <th className="px-4 py-2 text-right">Stop</th>
                <th className="px-4 py-2 text-right">Target</th>
                <th className="px-4 py-2 text-right">Fill</th>
                <th className="px-4 py-2 text-right">P&amp;L</th>
                <th className="px-4 py-2 text-right">Conf</th>
                <th className="px-4 py-2 text-center">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/60">
              {trades.map((trade) => (
                <tr key={trade.id} className="text-gray-300">
                  <td className="px-4 py-2 font-mono text-xs text-gray-500">
                    {formatETTime(trade.created_at)}
                  </td>
                  <td className="px-4 py-2 font-mono font-semibold text-gray-100">
                    {trade.symbol}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ${
                        trade.direction === 'bullish'
                          ? 'bg-emerald-900/40 text-emerald-400'
                          : 'bg-red-900/40 text-red-400'
                      }`}
                    >
                      {trade.direction === 'bullish' ? '↑' : '↓'} {trade.direction}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right font-mono">{trade.qty}</td>
                  <td className="px-4 py-2 text-right font-mono">{fmtPrice(trade.entry_price)}</td>
                  <td className="px-4 py-2 text-right font-mono text-red-400">
                    {fmtPrice(trade.stop_price)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-emerald-400">
                    {fmtPrice(trade.take_profit_price)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-gray-400">
                    {trade.fill_price != null ? fmtPrice(trade.fill_price) : '—'}
                  </td>
                  <td className="px-4 py-2 text-right font-mono">
                    {trade.pnl_pct != null ? (
                      <span className={trade.pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                        {fmtPnl(trade.pnl_pct)}
                      </span>
                    ) : (
                      <span className="text-gray-600">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-gray-400">
                    {trade.bias_confidence != null ? fmtConfidence(trade.bias_confidence) : '—'}
                  </td>
                  <td className="px-4 py-2 text-center">
                    <span
                      className={`inline-flex rounded px-1.5 py-0.5 text-xs font-medium ${
                        trade.status === 'open'
                          ? 'bg-blue-900/40 text-blue-400'
                          : trade.status === 'closed'
                          ? 'bg-gray-800 text-gray-400'
                          : 'bg-yellow-900/40 text-yellow-400'
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
  )
}
