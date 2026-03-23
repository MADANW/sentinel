// Server Component — no 'use client' needed

interface KillSwitchBannerProps {
  active: boolean
}

/**
 * Full-width red banner displayed ONLY when the daily loss limit has been reached.
 * Conditionally rendered in JSX — never hidden via CSS.
 */
export default function KillSwitchBanner({ active }: KillSwitchBannerProps) {
  if (!active) return null

  return (
    <div className="mb-6 rounded-lg border border-red-800 bg-red-950/60 px-5 py-4">
      <p className="font-mono text-sm font-semibold text-red-400">
        ⛔ Kill switch active — daily loss limit reached. No new orders will be submitted.
      </p>
    </div>
  )
}
