// Server Component — no 'use client' needed

import type { PipelineRun } from '@/lib/types'

interface PipelineGatesProps {
  run: PipelineRun | null
}

type GateState = 'pass' | 'fail' | 'pending'

function gateClasses(state: GateState): string {
  switch (state) {
    case 'pass':
      return 'border-emerald-800 bg-emerald-950/30'
    case 'fail':
      return 'border-red-800 bg-red-950/30'
    case 'pending':
      return 'border-gray-800 bg-gray-900'
  }
}

function gateLabelClasses(state: GateState): string {
  switch (state) {
    case 'pass':    return 'text-emerald-400'
    case 'fail':    return 'text-red-400'
    case 'pending': return 'text-gray-500'
  }
}

function gateIcon(state: GateState): string {
  switch (state) {
    case 'pass':    return '✓'
    case 'fail':    return '✗'
    case 'pending': return '—'
  }
}

interface GateCardProps {
  label: string
  state: GateState
  value: string
}

function GateCard({ label, state, value }: GateCardProps) {
  return (
    <div className={`rounded-lg border p-4 ${gateClasses(state)}`}>
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-wider text-gray-400">{label}</p>
        <span className={`font-mono text-sm font-bold ${gateLabelClasses(state)}`}>
          {gateIcon(state)}
        </span>
      </div>
      <p className={`font-mono text-sm ${gateLabelClasses(state)}`}>{value}</p>
    </div>
  )
}

/**
 * Three gate status cards: ML model, Monte Carlo, Claude veto.
 * Reads from the latest pipeline_run row. Gray/pending if no run today.
 */
export default function PipelineGates({ run }: PipelineGatesProps) {
  // ML gate
  let mlState: GateState = 'pending'
  let mlValue = '—'
  if (run?.ml_probability != null) {
    const p = run.ml_probability
    const passed = p >= 0.60 || p <= 0.40
    mlState = passed ? 'pass' : 'fail'
    mlValue = `p = ${p.toFixed(2)}`
    if (run.ml_signal) mlValue += ` · ${run.ml_signal}`
  }

  // Monte Carlo gate
  let mcState: GateState = 'pending'
  let mcValue = '—'
  if (run?.mc_hit_rate != null) {
    mcState = run.mc_passed ? 'pass' : 'fail'
    mcValue = `hit rate = ${(run.mc_hit_rate * 100).toFixed(1)}%`
  }

  // Claude gate
  let claudeState: GateState = 'pending'
  let claudeValue = '—'
  if (run?.claude_approved != null) {
    claudeState = run.claude_approved ? 'pass' : 'fail'
    claudeValue = run.claude_approved ? 'approved' : 'vetoed'
    if (run.claude_reason) claudeValue += ` · ${run.claude_reason.slice(0, 60)}`
  }

  return (
    <div className="mb-6">
      <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-500">
        Pipeline gates
        {run && (
          <span className="ml-2 font-mono font-normal normal-case text-gray-600">
            {run.ticker} ·{' '}
            {new Date(run.created_at).toLocaleTimeString('en-US', {
              hour: '2-digit',
              minute: '2-digit',
              timeZone: 'America/New_York',
            })}{' '}
            ET
          </span>
        )}
      </p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <GateCard label="ML model" state={mlState} value={mlValue} />
        <GateCard label="Monte Carlo" state={mcState} value={mcValue} />
        <GateCard label="Claude veto" state={claudeState} value={claudeValue} />
      </div>
    </div>
  )
}
