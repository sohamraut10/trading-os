import { useState, useRef, useCallback, useEffect } from 'react'

// ─── Types ────────────────────────────────────────────────────────────────────

export type OmegaTaskState = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED'

export interface OmegaTask {
  id: string
  goal: string
  state: OmegaTaskState
  dispatched: number
  progress: number
  summary?: string
}

// ─── Colors ───────────────────────────────────────────────────────────────────

const TASK_COLOR: Record<OmegaTaskState, string> = {
  PENDING: '#8B8FA8',
  RUNNING: '#8A5CFF',
  COMPLETED: '#38E1FF',
  FAILED: '#D96BFF',
}

// ─── Preset goals ─────────────────────────────────────────────────────────────

export const PRESET_GOALS = [
  'Deep market analysis on TSLA with a 3-day trading plan',
  'Optimize portfolio allocation for Q3 based on current positions',
  'Research upcoming earnings affecting my holdings and risk exposure',
  'Build a Monte Carlo simulation of my retirement trajectory',
]

// ─── Mock summaries ───────────────────────────────────────────────────────────

function buildSummary(goal: string): string {
  const g = goal.toLowerCase()
  if (g.includes('tsla') || g.includes('trading plan')) {
    return `**TSLA 3-Day Trading Plan — Jul 18–20**

Current bias: **Bullish** (+2.3% vs SPX this week). Key levels:
- Resistance: **$248.40** (Jul 12 pivot high)
- Support: **$231.80** (200D EMA)

**Day 1 (Jul 18):** Hold long from $238. Target $246. Hard stop $234.
**Day 2 (Jul 19):** Watch pre-market volume. If gap up > 1.5%, trim 30% above $248.
**Day 3 (Jul 20):** Re-enter on pullback to $241 if RSI < 52. Probability of continuation: **61%**.

Risk/reward: 2.4:1. Position size: 4% of portfolio.`
  }
  if (g.includes('portfolio') || g.includes('allocation')) {
    return `**Portfolio Optimization — Q3 2026**

Current allocation vs. optimal (Sharpe-maximized):

| Asset | Current | Target | Delta |
|-------|---------|--------|-------|
| US Equities | 62% | 54% | −8% |
| Int'l Equities | 8% | 14% | +6% |
| Fixed Income | 18% | 20% | +2% |
| Alternatives | 5% | 8% | +3% |
| Cash | 7% | 4% | −3% |

**Priority rebalance:** Reduce tech concentration (NVDA, MSFT at 31% combined). Suggested: trim $2,100 from NVDA, deploy into VEA.`
  }
  if (g.includes('earnings')) {
    return `**Upcoming Earnings — Holdings Impact Analysis**

High impact on your portfolio (next 14 days):

- **NVDA** (Jul 23, AMC) — Consensus EPS $0.64. You hold 12 shares. IV: 68%. Suggested: sell 1 covered call at $460 strike.
- **MSFT** (Jul 25, AMC) — Consensus EPS $3.10. Low risk, no action needed.
- **AMZN** (Aug 1, AMC) — Consensus EPS $1.03. Whisper higher. Consider adding 2 shares pre-earnings.

Net portfolio earnings exposure: **$4,280** notional. Recommended hedge: SPY put spread expiring Aug 2.`
  }
  if (g.includes('monte carlo') || g.includes('retirement')) {
    return `**Retirement Monte Carlo Simulation — 10,000 Scenarios**

Inputs: $24,891 current NW · $3,200/mo income · $1,650/mo spend · 7% avg return · retire at 58.

| Outcome | Probability |
|---------|------------|
| Retire at 58 with $2M+ | 34% |
| Retire at 60 with $1.5M+ | 61% |
| Retire at 65+ | 22% |
| Shortfall risk | 8% |

**Key lever:** Increasing savings rate by $400/mo improves P(retire at 58) from 34% → 51%. Recommended: redirect Netflix + Spotify spend to HYSA.`
  }
  return `**Research Complete**

Omega analyzed your request across 14 data sources. The findings indicate a moderate-risk opportunity aligned with your current portfolio profile.

Key action items extracted and queued. Full report available in the Omega dashboard on port 3005.`
}

// ─── Elapsed time ─────────────────────────────────────────────────────────────

function useElapsed(start: number, active: boolean) {
  const [secs, setSecs] = useState(0)
  useEffect(() => {
    if (!active) { setSecs(0); return }
    const id = setInterval(() => setSecs(Math.floor((Date.now() - start) / 1000)), 1000)
    return () => clearInterval(id)
  }, [start, active])
  return secs
}

// ─── OmegaTaskCard ────────────────────────────────────────────────────────────

function OmegaTaskCard({ task }: { task: OmegaTask }) {
  const [expanded, setExpanded] = useState(false)
  const c = TASK_COLOR[task.state]
  const elapsed = useElapsed(task.dispatched, task.state === 'RUNNING')

  const stateLabel: Record<OmegaTaskState, string> = {
    PENDING: 'Queued',
    RUNNING: `Running · ${elapsed}s`,
    COMPLETED: 'Done',
    FAILED: 'Failed',
  }

  return (
    <div
      style={{
        borderRadius: 16,
        border: `1px solid ${task.state === 'COMPLETED' ? c + '30' : 'rgba(255,255,255,0.07)'}`,
        background: 'rgba(8,8,22,0.7)',
        backdropFilter: 'blur(12px)',
        overflow: 'hidden',
        transition: 'border-color 0.5s ease',
      }}
    >
      {/* Header row */}
      <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          {/* Task ID chip */}
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              padding: '3px 8px',
              borderRadius: 999,
              border: `1px solid ${c}40`,
              background: `${c}0D`,
            }}
          >
            <div
              style={{
                width: 5,
                height: 5,
                borderRadius: '50%',
                background: c,
                boxShadow: task.state === 'RUNNING' ? `0 0 5px ${c}` : 'none',
                animation: task.state === 'RUNNING' ? 'dot-blink 1.2s ease-in-out infinite' : 'none',
              }}
            />
            <span
              style={{
                fontSize: 9,
                fontFamily: 'Space Grotesk, sans-serif',
                fontWeight: 600,
                letterSpacing: '0.2em',
                color: c,
              }}
            >
              {task.id}
            </span>
          </div>

          {/* State badge */}
          <span
            style={{
              fontSize: 9,
              fontFamily: 'Space Grotesk, sans-serif',
              fontWeight: 500,
              letterSpacing: '0.3em',
              textTransform: 'uppercase',
              color: c,
              transition: 'color 0.5s ease',
            }}
          >
            {stateLabel[task.state]}
          </span>
        </div>

        {/* Goal text */}
        <p
          style={{
            margin: 0,
            fontSize: 12,
            fontFamily: 'Space Grotesk, sans-serif',
            fontWeight: 400,
            color: '#E8E9F2',
            lineHeight: 1.5,
            opacity: task.state === 'PENDING' ? 0.6 : 0.9,
          }}
        >
          {task.goal}
        </p>

        {/* Progress bar — RUNNING only */}
        {task.state === 'RUNNING' && (
          <div
            style={{
              height: 2,
              borderRadius: 2,
              background: 'rgba(255,255,255,0.07)',
              overflow: 'hidden',
              marginTop: 2,
            }}
          >
            <div
              style={{
                height: '100%',
                width: `${task.progress}%`,
                background: `linear-gradient(90deg, ${c}80, ${c})`,
                borderRadius: 2,
                transition: 'width 0.4s ease',
                boxShadow: `0 0 8px ${c}60`,
              }}
            />
          </div>
        )}

        {/* Completed: expand toggle */}
        {task.state === 'COMPLETED' && task.summary && (
          <button
            onClick={() => setExpanded((e) => !e)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              padding: 0,
              marginTop: 2,
            }}
          >
            <span
              style={{
                fontSize: 9,
                fontFamily: 'Space Grotesk, sans-serif',
                fontWeight: 500,
                letterSpacing: '0.3em',
                textTransform: 'uppercase',
                color: c,
              }}
            >
              {expanded ? 'Hide Report' : 'View Report'}
            </span>
            <svg
              width="10"
              height="10"
              viewBox="0 0 10 10"
              fill="none"
              style={{
                transform: expanded ? 'rotate(180deg)' : 'none',
                transition: 'transform 0.3s ease',
              }}
            >
              <path d="M2 3.5L5 6.5L8 3.5" stroke={c} strokeWidth="1.2" strokeLinecap="round" />
            </svg>
          </button>
        )}
      </div>

      {/* Summary — expanded */}
      {task.state === 'COMPLETED' && expanded && task.summary && (
        <div
          style={{
            borderTop: `1px solid rgba(255,255,255,0.06)`,
            padding: '14px',
            animation: 'fade-in 0.3s ease both',
          }}
        >
          <pre
            style={{
              margin: 0,
              fontFamily: 'Space Grotesk, sans-serif',
              fontSize: 11,
              lineHeight: 1.7,
              color: '#8B8FA8',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {task.summary}
          </pre>
        </div>
      )}
    </div>
  )
}

// ─── useOmega hook ────────────────────────────────────────────────────────────

export function useOmega() {
  const [tasks, setTasks] = useState<OmegaTask[]>([])
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([])

  const dispatch = useCallback((goal: string) => {
    const id = `OMG-${Math.random().toString(36).slice(2, 6).toUpperCase()}`
    const now = Date.now()

    setTasks((prev) => [
      { id, goal, state: 'PENDING', dispatched: now, progress: 0 },
      ...prev,
    ])

    // PENDING → RUNNING
    const t1 = setTimeout(() => {
      setTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, state: 'RUNNING', dispatched: Date.now() } : t)),
      )

      // Animate progress
      let p = 0
      const tick = () => {
        p = Math.min(p + Math.random() * 7 + 2, 94)
        setTasks((prev) => prev.map((t) => (t.id === id ? { ...t, progress: p } : t)))
        if (p < 94) {
          const tid = setTimeout(tick, 350 + Math.random() * 250)
          timersRef.current.push(tid)
        }
      }
      const pt = setTimeout(tick, 300)
      timersRef.current.push(pt)

      // RUNNING → COMPLETED
      const duration = 4500 + Math.random() * 2000
      const t2 = setTimeout(() => {
        setTasks((prev) =>
          prev.map((t) =>
            t.id === id
              ? { ...t, state: 'COMPLETED', progress: 100, summary: buildSummary(goal) }
              : t,
          ),
        )
      }, duration)
      timersRef.current.push(t2)
    }, 900)

    timersRef.current.push(t1)
  }, [])

  const clearAll = useCallback(() => {
    timersRef.current.forEach(clearTimeout)
    timersRef.current = []
    setTasks([])
  }, [])

  useEffect(() => () => timersRef.current.forEach(clearTimeout), [])

  const runningCount = tasks.filter((t) => t.state === 'RUNNING' || t.state === 'PENDING').length

  return { tasks, dispatch, clearAll, runningCount }
}

// ─── OmegaPanel ──────────────────────────────────────────────────────────────

export function OmegaPanel({
  tasks,
  onDispatch,
  compact,
}: {
  tasks: OmegaTask[]
  onDispatch: (goal: string) => void
  compact?: boolean
}) {
  const [input, setInput] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const submit = useCallback(() => {
    const goal = input.trim()
    if (!goal) return
    onDispatch(goal)
    setInput('')
  }, [input, onDispatch])

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') submit()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Panel header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {/* Omega symbol */}
          <div
            style={{
              width: 22,
              height: 22,
              borderRadius: '50%',
              border: '1px solid rgba(138,92,255,0.4)',
              background: 'rgba(138,92,255,0.1)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 11,
              color: '#8A5CFF',
              fontFamily: 'Space Grotesk, sans-serif',
              fontWeight: 700,
            }}
          >
            Ω
          </div>
          <span
            style={{
              fontSize: 9,
              fontFamily: 'Space Grotesk, sans-serif',
              fontWeight: 500,
              letterSpacing: '0.35em',
              textTransform: 'uppercase',
              color: '#8B8FA8',
            }}
          >
            Omega Cluster
          </span>
        </div>
        {tasks.length > 0 && (
          <span
            style={{
              fontSize: 9,
              fontFamily: 'Space Grotesk, sans-serif',
              letterSpacing: '0.2em',
              textTransform: 'uppercase',
              color: 'rgba(139,143,168,0.45)',
            }}
          >
            {tasks.length} task{tasks.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Input row */}
      <div
        style={{
          display: 'flex',
          gap: 8,
          alignItems: 'center',
          background: 'rgba(8,8,22,0.8)',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: 999,
          padding: '10px 10px 10px 16px',
        }}
      >
        <input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Describe a complex task for Omega…"
          style={{
            flex: 1,
            background: 'none',
            border: 'none',
            outline: 'none',
            fontFamily: 'Space Grotesk, sans-serif',
            fontSize: 12,
            fontWeight: 400,
            color: '#E8E9F2',
            letterSpacing: '0.01em',
          }}
        />
        <button
          onClick={submit}
          disabled={!input.trim()}
          style={{
            width: 30,
            height: 30,
            borderRadius: '50%',
            border: 'none',
            background: input.trim() ? '#8A5CFF' : 'rgba(138,92,255,0.15)',
            cursor: input.trim() ? 'pointer' : 'default',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
            transition: 'background 0.3s ease',
          }}
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path d="M2 6H10M10 6L7 3M10 6L7 9" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>

      {/* Preset goals */}
      {!compact && tasks.length === 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span
            style={{
              fontSize: 9,
              fontFamily: 'Space Grotesk, sans-serif',
              fontWeight: 500,
              letterSpacing: '0.35em',
              textTransform: 'uppercase',
              color: 'rgba(139,143,168,0.45)',
              marginBottom: 2,
            }}
          >
            Suggested
          </span>
          {PRESET_GOALS.map((goal) => (
            <button
              key={goal}
              onClick={() => onDispatch(goal)}
              style={{
                background: 'rgba(8,8,22,0.5)',
                border: '1px solid rgba(255,255,255,0.06)',
                borderRadius: 10,
                padding: '9px 12px',
                textAlign: 'left',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}
            >
              <span style={{ fontSize: 10, color: 'rgba(138,92,255,0.6)' }}>→</span>
              <span
                style={{
                  fontSize: 11,
                  fontFamily: 'Space Grotesk, sans-serif',
                  fontWeight: 400,
                  color: '#8B8FA8',
                  lineHeight: 1.4,
                }}
              >
                {goal}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* Task list */}
      {tasks.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {tasks.map((task) => (
            <OmegaTaskCard key={task.id} task={task} />
          ))}
        </div>
      )}
    </div>
  )
}

// ─── OmegaHeaderBadge ─────────────────────────────────────────────────────────

export function OmegaHeaderBadge({
  runningCount,
  open,
  onToggle,
}: {
  runningCount: number
  open: boolean
  onToggle: () => void
}) {
  const active = runningCount > 0

  return (
    <button
      onClick={onToggle}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '5px 12px 5px 8px',
        background: open
          ? 'rgba(138,92,255,0.15)'
          : 'rgba(10,10,26,0.7)',
        backdropFilter: 'blur(12px)',
        border: `1px solid ${open ? 'rgba(138,92,255,0.4)' : 'rgba(255,255,255,0.08)'}`,
        borderRadius: 999,
        cursor: 'pointer',
        transition: 'all 0.3s ease',
        outline: 'none',
      }}
    >
      <div
        style={{
          width: 16,
          height: 16,
          borderRadius: '50%',
          border: '1px solid rgba(138,92,255,0.5)',
          background: 'rgba(138,92,255,0.15)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 8,
          color: '#8A5CFF',
          fontFamily: 'Space Grotesk, sans-serif',
          fontWeight: 700,
          position: 'relative',
        }}
      >
        Ω
        {active && (
          <div
            style={{
              position: 'absolute',
              top: -2,
              right: -2,
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: '#8A5CFF',
              boxShadow: '0 0 6px #8A5CFF',
              animation: 'dot-blink 1.2s ease-in-out infinite',
            }}
          />
        )}
      </div>
      <span
        style={{
          fontSize: 9,
          fontFamily: 'Space Grotesk, sans-serif',
          fontWeight: 500,
          letterSpacing: '0.3em',
          textTransform: 'uppercase',
          color: open ? '#8A5CFF' : '#8B8FA8',
          transition: 'color 0.3s ease',
        }}
      >
        {active ? `${runningCount} Running` : 'Omega'}
      </span>
    </button>
  )
}
