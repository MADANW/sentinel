// Server Component — no 'use client' needed

interface HeaderProps {
  accountNumber: string
  tradingEnv: string
  marketOpen: boolean
}

/**
 * Top header bar: app name, account number, trading mode badge, market status.
 * The only allowed animation is the market-open pulse dot (animate-pulse).
 */
export default function Header({ accountNumber, tradingEnv, marketOpen }: HeaderProps) {
  const isLive = tradingEnv === 'live'

  return (
    <header className="mb-6 flex items-center justify-between">
      {/* Left: app name + account */}
      <div className="flex items-baseline gap-4">
        <h1 className="font-mono text-xl font-bold tracking-tight text-gray-100">
          Sentinel
        </h1>
        {accountNumber !== '—' && (
          <span className="font-mono text-xs text-gray-500">#{accountNumber}</span>
        )}
      </div>

      {/* Right: badges */}
      <div className="flex items-center gap-3">
        {/* Market status */}
        <div className="flex items-center gap-1.5">
          {marketOpen ? (
            <>
              {/* Only allowed animation: pulse dot when market is open */}
              <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-emerald-400" />
              <span className="font-mono text-xs font-medium text-emerald-400">OPEN</span>
            </>
          ) : (
            <>
              <span className="inline-block h-2 w-2 rounded-full bg-gray-600" />
              <span className="font-mono text-xs font-medium text-gray-500">CLOSED</span>
            </>
          )}
        </div>

        {/* Trading mode badge */}
        <span
          className={`rounded px-2 py-0.5 font-mono text-xs font-semibold ${
            isLive
              ? 'bg-red-900/60 text-red-400 border border-red-800'
              : 'bg-amber-900/40 text-amber-400 border border-amber-800/60'
          }`}
        >
          {isLive ? 'LIVE' : 'PAPER'}
        </span>
      </div>
    </header>
  )
}
