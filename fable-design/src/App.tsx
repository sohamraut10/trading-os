import { useState, useEffect, useRef, useCallback } from 'react'
import {
  OmegaPanel,
  OmegaHeaderBadge,
  useOmega,
} from './OmegaPanel'

type AppState = 'idle' | 'listening' | 'thinking' | 'speaking'

const COLOR: Record<AppState, string> = {
  idle: '#5B8CFF',
  listening: '#38E1FF',
  thinking: '#8A5CFF',
  speaking: '#D96BFF',
}

const LABEL: Record<AppState, string> = {
  idle: 'Standby',
  listening: 'Listening',
  thinking: 'Thinking',
  speaking: 'Speaking',
}

const TRANSCRIPT: Record<AppState, string> = {
  idle: 'Touch the orb to speak with Fable',
  listening: 'How much did I spend on dining last month?',
  thinking: 'Analyzing your June transactions…',
  speaking: 'You spent $847 on dining — 23% above your $690 goal.',
}

const SEQ: AppState[] = ['idle', 'listening', 'thinking', 'speaking']
const DURATIONS: Record<AppState, number> = {
  idle: 0,
  listening: 2200,
  thinking: 1800,
  speaking: 3600,
}

const DUST = Array.from({ length: 30 }, (_, i) => {
  const angle = (i / 30) * Math.PI * 2 + Math.sin(i * 1.3) * 0.6
  const radial = 1.42 + (i % 5) * 0.08 - (i % 3) * 0.04
  const yScale = 0.55 + (i % 4) * 0.06
  return {
    x: Math.cos(angle) * radial,
    y: Math.sin(angle) * radial * yScale,
    r: 0.7 + (i % 4) * 0.55,
    o: 0.22 + (i % 5) * 0.12,
    td: `${(i * 0.18).toFixed(2)}s`,
  }
})

const TRANSACTIONS = [
  { name: 'Whole Foods Market', cat: 'Groceries', amount: -89.43, date: 'Today', positive: false },
  { name: 'Direct Deposit', cat: 'Income', amount: 3200.0, date: 'Jul 15', positive: true },
  { name: 'Erewhon', cat: 'Dining', amount: -67.2, date: 'Jul 14', positive: false },
  { name: 'Netflix', cat: 'Subscriptions', amount: -15.99, date: 'Jul 13', positive: false },
]

const SPENDING_CATS = [
  { label: 'Dining', pct: 62, amount: 847 },
  { label: 'Groceries', pct: 45, amount: 312 },
  { label: 'Transport', pct: 78, amount: 156 },
  { label: 'Subs', pct: 30, amount: 89 },
]

// ─── Orb ──────────────────────────────────────────────────────────────────────

function Orb({ state, size }: { state: AppState; size: number }) {
  const c = COLOR[state]
  const cx = size / 2
  const cy = size / 2
  const r = size * 0.315

  return (
    <div
      className={`orb-${state}`}
      style={{ position: 'relative', width: size, height: size, flexShrink: 0, cursor: 'pointer' }}
    >
      <div
        style={{
          position: 'absolute',
          inset: -size * 0.28,
          borderRadius: '50%',
          background: `radial-gradient(circle at 50% 50%, ${c}28 0%, ${c}10 38%, transparent 68%)`,
          filter: `blur(${size * 0.14}px)`,
          pointerEvents: 'none',
        }}
      />
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ position: 'relative', overflow: 'visible' }}>
        <defs>
          <radialGradient id={`sg-${state}`} cx="37%" cy="30%" r="72%">
            <stop offset="0%" stopColor="#080818" stopOpacity="0.97" />
            <stop offset="28%" stopColor={c} stopOpacity="0.06" />
            <stop offset="62%" stopColor={c} stopOpacity="0.48" />
            <stop offset="88%" stopColor={c} stopOpacity="0.78" />
            <stop offset="100%" stopColor={c} stopOpacity="0.92" />
          </radialGradient>
          <filter id={`gf-${state}`} x="-90%" y="-90%" width="280%" height="280%">
            <feGaussianBlur stdDeviation={r * 0.2} result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <filter id="df" x="-200%" y="-200%" width="500%" height="500%">
            <feGaussianBlur stdDeviation="1.6" result="b" />
            <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <radialGradient id={`rim-${state}`} cx="50%" cy="50%" r="50%">
            <stop offset="72%" stopColor="transparent" />
            <stop offset="90%" stopColor={c} stopOpacity="0.35" />
            <stop offset="100%" stopColor={c} stopOpacity="0.55" />
          </radialGradient>
        </defs>
        {DUST.map((p, i) => (
          <circle key={i} cx={cx + p.x * r} cy={cy + p.y * r} r={p.r} fill={c} fillOpacity={p.o} filter="url(#df)"
            style={{ animation: `dust-twinkle ${2.2 + (i % 4) * 0.7}s ease-in-out ${p.td} infinite` }} />
        ))}
        <ellipse cx={cx} cy={cy} rx={r * 1.54} ry={r * 0.21} fill="none" stroke={c} strokeWidth="0.65" strokeOpacity="0.4"
          style={{
            transformOrigin: `${cx}px ${cy}px`,
            animation: state === 'thinking' ? 'ring-orbit 8s linear infinite' : 'none',
            transform: state !== 'thinking' ? 'rotate(18deg)' : undefined,
          }} />
        <ellipse cx={cx} cy={cy} rx={r * 1.78} ry={r * 0.30} fill="none" stroke={c} strokeWidth="0.45" strokeOpacity="0.22"
          style={{
            transformOrigin: `${cx}px ${cy}px`,
            animation: state === 'thinking' ? 'ring-orbit-rev 12s linear infinite' : 'none',
            transform: state !== 'thinking' ? 'rotate(-14deg)' : undefined,
          }} />
        <circle cx={cx} cy={cy} r={r} fill={`url(#sg-${state})`} filter={`url(#gf-${state})`} />
        <circle cx={cx} cy={cy} r={r} fill={`url(#rim-${state})`} />
        <ellipse cx={cx - r * 0.16} cy={cy - r * 0.2} rx={r * 0.36} ry={r * 0.2} fill="white" fillOpacity="0.045" />
      </svg>
    </div>
  )
}

// ─── Waveform ─────────────────────────────────────────────────────────────────

function Waveform({ state }: { state: AppState }) {
  const active = state === 'listening' || state === 'speaking'
  const color = COLOR[state]
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 2.5, height: 36, padding: '0 4px' }}>
      {Array.from({ length: 32 }, (_, i) => (
        <div key={i} style={{
          width: 2, height: '100%', borderRadius: 2,
          background: active ? color : 'rgba(139,143,168,0.25)',
          transformOrigin: 'center',
          transform: active ? undefined : 'scaleY(0.12)',
          animation: active ? `wave-bar ${0.4 + (i % 7) * 0.09}s ease-in-out ${(i % 11) * 0.06}s infinite` : 'none',
          opacity: active ? 0.8 : 0.35,
          transition: 'background 0.6s ease, opacity 0.6s ease',
        }} />
      ))}
    </div>
  )
}

// ─── State badge ──────────────────────────────────────────────────────────────

function StateBadge({ state }: { state: AppState }) {
  const c = COLOR[state]
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 6,
      padding: '5px 12px 5px 8px',
      background: 'rgba(10,10,26,0.7)', backdropFilter: 'blur(12px)',
      border: `1px solid ${c}30`, borderRadius: 999, transition: 'border-color 0.6s ease',
    }}>
      <div style={{
        width: 6, height: 6, borderRadius: '50%',
        background: c, boxShadow: `0 0 6px ${c}`,
        animation: 'dot-blink 1.6s ease-in-out infinite',
      }} />
      <span style={{
        fontSize: 9, fontFamily: 'Space Grotesk, sans-serif', fontWeight: 500,
        letterSpacing: '0.32em', textTransform: 'uppercase', color: c,
        transition: 'color 0.6s ease',
      }}>
        {LABEL[state]}
      </span>
    </div>
  )
}

// ─── Pill surface ─────────────────────────────────────────────────────────────

function Pill({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{
      background: 'rgba(10,10,26,0.6)', backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)',
      border: '1px solid rgba(255,255,255,0.08)', borderRadius: 999, padding: '14px 20px', ...style,
    }}>
      {children}
    </div>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <span style={{
      fontSize: 9, fontWeight: 500, letterSpacing: '0.35em', textTransform: 'uppercase' as const,
      color: '#8B8FA8', fontFamily: 'Space Grotesk, sans-serif',
    }}>{children}</span>
  )
}

function SpendBar({ label, pct, amount, color }: { label: string; pct: number; amount: number; color: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <Label>{label}</Label>
        <span style={{ fontSize: 11, color: '#E8E9F2', fontFamily: 'Space Grotesk, sans-serif' }}>${amount}</span>
      </div>
      <div style={{ height: 2, borderRadius: 2, background: 'rgba(255,255,255,0.07)', overflow: 'hidden' }}>
        <div style={{
          height: '100%', width: `${pct}%`,
          background: pct > 70 ? COLOR.speaking : pct > 50 ? COLOR.thinking : color,
          borderRadius: 2, transition: 'width 1s cubic-bezier(0.4,0,0.2,1)',
        }} />
      </div>
    </div>
  )
}

function TxRow({ name, cat, amount, date, positive }: (typeof TRANSACTIONS)[number]) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '10px 0', borderBottom: '1px solid rgba(255,255,255,0.05)',
    }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ fontSize: 13, color: '#E8E9F2', fontWeight: 500, fontFamily: 'Space Grotesk, sans-serif' }}>{name}</span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Label>{cat}</Label>
          <span style={{ fontSize: 9, color: 'rgba(139,143,168,0.5)' }}>·</span>
          <Label>{date}</Label>
        </div>
      </div>
      <span style={{
        fontSize: 14, fontWeight: 600, fontFamily: 'Space Grotesk, sans-serif',
        color: positive ? COLOR.listening : '#E8E9F2', letterSpacing: '-0.01em',
      }}>
        {positive ? '+' : ''}${Math.abs(amount).toFixed(2)}
      </span>
    </div>
  )
}

function MicButton({ state, onTap }: { state: AppState; onTap: () => void }) {
  const c = COLOR[state]
  const isActive = state !== 'idle'
  return (
    <button onClick={onTap} style={{
      width: '100%', maxWidth: 280, padding: '16px 24px', borderRadius: 999,
      border: `1px solid ${isActive ? c + '55' : 'rgba(255,255,255,0.1)'}`,
      background: isActive ? `linear-gradient(135deg, ${c}18, ${c}08)` : 'rgba(10,10,26,0.8)',
      backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)',
      cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
      gap: 10, transition: 'all 0.5s ease', outline: 'none',
    }}>
      <svg width="16" height="20" viewBox="0 0 16 20" fill="none">
        <rect x="4" y="0" width="8" height="12" rx="4" fill={isActive ? c : '#8B8FA8'} />
        <path d="M1 9c0 3.866 3.134 7 7 7s7-3.134 7-7" stroke={isActive ? c : '#8B8FA8'} strokeWidth="1.5" strokeLinecap="round" fill="none" />
        <line x1="8" y1="16" x2="8" y2="20" stroke={isActive ? c : '#8B8FA8'} strokeWidth="1.5" strokeLinecap="round" />
        <line x1="5" y1="20" x2="11" y2="20" stroke={isActive ? c : '#8B8FA8'} strokeWidth="1.5" strokeLinecap="round" />
      </svg>
      <span style={{
        fontSize: 11, fontWeight: 500, letterSpacing: '0.28em', textTransform: 'uppercase',
        color: isActive ? c : '#8B8FA8', fontFamily: 'Space Grotesk, sans-serif', transition: 'color 0.5s ease',
      }}>
        {state === 'idle' ? 'Tap to Speak' : state === 'listening' ? 'Listening…' : state === 'thinking' ? 'Processing…' : 'Speaking…'}
      </span>
    </button>
  )
}

// ─── Omega drawer (mobile) ────────────────────────────────────────────────────

function OmegaDrawer({
  open,
  tasks,
  onDispatch,
}: {
  open: boolean
  tasks: ReturnType<typeof useOmega>['tasks']
  onDispatch: (goal: string) => void
}) {
  return (
    <div style={{
      position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 50,
      background: 'rgba(5,5,18,0.97)', backdropFilter: 'blur(24px)',
      border: '1px solid rgba(138,92,255,0.2)', borderRadius: '24px 24px 0 0',
      padding: '20px 20px 36px',
      transform: open ? 'translateY(0)' : 'translateY(110%)',
      transition: 'transform 0.4s cubic-bezier(0.4,0,0.2,1)',
      maxHeight: '70dvh', overflowY: 'auto',
    }}>
      {/* Drag handle */}
      <div style={{
        width: 36, height: 3, borderRadius: 2,
        background: 'rgba(255,255,255,0.12)', margin: '0 auto 20px',
      }} />
      <OmegaPanel tasks={tasks} onDispatch={onDispatch} compact={tasks.length > 0} />
    </div>
  )
}

// ─── Mobile layout ────────────────────────────────────────────────────────────

function MobileView({
  state, onOrbTap, omega, omegaOpen, onOmegaToggle,
}: {
  state: AppState
  onOrbTap: () => void
  omega: ReturnType<typeof useOmega>
  omegaOpen: boolean
  onOmegaToggle: () => void
}) {
  const c = COLOR[state]
  return (
    <>
      <div style={{
        width: '100%', maxWidth: 393, minHeight: '100dvh',
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        padding: '52px 24px 40px', position: 'relative', gap: 0,
      }}>
        {/* Header */}
        <div style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 32 }}>
          <span style={{
            fontFamily: 'Michroma, sans-serif', fontSize: 18, letterSpacing: '0.9em',
            color: '#E8E9F2', fontWeight: 400, textTransform: 'uppercase',
          }}>FABLE</span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <OmegaHeaderBadge runningCount={omega.runningCount} open={omegaOpen} onToggle={onOmegaToggle} />
            <StateBadge state={state} />
          </div>
        </div>

        {/* Orb */}
        <div onClick={onOrbTap} style={{ cursor: 'pointer', marginBottom: 8, userSelect: 'none' }}>
          <Orb state={state} size={216} />
        </div>

        {/* Transcript */}
        <div key={state} style={{ marginBottom: 10, textAlign: 'center', animation: 'fade-in 0.5s ease both' }}>
          <p style={{
            fontSize: 13, color: state === 'idle' ? '#8B8FA8' : '#E8E9F2',
            fontFamily: 'Space Grotesk, sans-serif', fontWeight: state === 'idle' ? 400 : 500,
            letterSpacing: '0.01em', lineHeight: 1.5, margin: 0, transition: 'color 0.5s ease', maxWidth: 280,
          }}>{TRANSCRIPT[state]}</p>
        </div>

        <div style={{ marginBottom: 28, opacity: state === 'idle' ? 0.4 : 1, transition: 'opacity 0.5s ease' }}>
          <Waveform state={state} />
        </div>

        {/* Balance pill */}
        <div style={{ width: '100%', marginBottom: 10 }}>
          <Pill style={{ borderRadius: 24, padding: '18px 24px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <Label>Net Worth</Label>
                <span style={{ fontSize: 28, fontWeight: 600, letterSpacing: '-0.03em', color: '#E8E9F2', fontFamily: 'Space Grotesk, sans-serif', lineHeight: 1 }}>
                  $24,891<span style={{ fontSize: 16, color: '#8B8FA8', fontWeight: 400 }}>.42</span>
                </span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
                <Label>Month</Label>
                <span style={{ fontSize: 13, fontWeight: 500, color: COLOR.listening, fontFamily: 'Space Grotesk, sans-serif' }}>+$1,204</span>
              </div>
            </div>
          </Pill>
        </div>

        {/* Spending */}
        <div style={{ width: '100%', marginBottom: 10 }}>
          <Pill style={{ borderRadius: 24, padding: '18px 24px' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Label>July Spending</Label>
              {SPENDING_CATS.map((cat) => <SpendBar key={cat.label} {...cat} color={c} />)}
            </div>
          </Pill>
        </div>

        {/* Transactions */}
        <div style={{ width: '100%', marginBottom: 28 }}>
          <Pill style={{ borderRadius: 24, padding: '16px 24px 6px' }}>
            <div style={{ marginBottom: 8 }}><Label>Recent Activity</Label></div>
            {TRANSACTIONS.map((tx, i) => <TxRow key={i} {...tx} />)}
          </Pill>
        </div>

        <MicButton state={state} onTap={onOrbTap} />
      </div>

      {/* Omega drawer */}
      <OmegaDrawer open={omegaOpen} tasks={omega.tasks} onDispatch={omega.dispatch} />

      {/* Backdrop */}
      {omegaOpen && (
        <div
          onClick={onOmegaToggle}
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 49, backdropFilter: 'blur(2px)' }}
        />
      )}
    </>
  )
}

// ─── Desktop layout ───────────────────────────────────────────────────────────

function DesktopView({
  state, onOrbTap, omega, omegaOpen, onOmegaToggle,
}: {
  state: AppState
  onOrbTap: () => void
  omega: ReturnType<typeof useOmega>
  omegaOpen: boolean
  onOmegaToggle: () => void
}) {
  const c = COLOR[state]

  return (
    <div style={{
      width: '100%', minHeight: '100dvh',
      display: 'grid', gridTemplateColumns: '1fr 1fr 1fr',
      position: 'relative', overflow: 'hidden',
    }}>
      {/* Left — financial overview */}
      <div style={{
        display: 'flex', flexDirection: 'column', justifyContent: 'center',
        padding: '60px 48px 60px 64px', gap: 16,
        borderRight: '1px solid rgba(255,255,255,0.05)',
      }}>
        <div style={{ marginBottom: 28 }}>
          <span style={{
            fontFamily: 'Michroma, sans-serif', fontSize: 16, letterSpacing: '0.9em',
            color: '#E8E9F2', fontWeight: 400, textTransform: 'uppercase', opacity: 0.85,
          }}>FABLE</span>
        </div>

        <div style={{ marginBottom: 8 }}>
          <Label>Total Net Worth</Label>
          <div style={{
            fontSize: 48, fontWeight: 700, letterSpacing: '-0.04em', color: '#E8E9F2',
            fontFamily: 'Space Grotesk, sans-serif', lineHeight: 1.1, marginTop: 6,
          }}>
            $24,891<span style={{ fontSize: 24, color: '#8B8FA8', fontWeight: 400 }}>.42</span>
          </div>
          <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: COLOR.listening }} />
            <span style={{ fontSize: 12, color: COLOR.listening, fontFamily: 'Space Grotesk, sans-serif', fontWeight: 500 }}>
              +$1,204 this month
            </span>
          </div>
        </div>

        <div style={{ height: 1, background: 'rgba(255,255,255,0.06)', margin: '8px 0' }} />

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <Label>Accounts</Label>
          {[
            { name: 'Chase Checking', val: '$8,420.17', type: 'Checking' },
            { name: 'Robinhood', val: '$12,341.89', type: 'Brokerage' },
            { name: 'High-Yield Savings', val: '$4,129.36', type: 'Savings' },
          ].map((acc) => (
            <div key={acc.name} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '10px 14px', borderRadius: 12,
              background: 'rgba(10,10,26,0.4)', border: '1px solid rgba(255,255,255,0.06)',
            }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <span style={{ fontSize: 12, color: '#E8E9F2', fontFamily: 'Space Grotesk, sans-serif', fontWeight: 500 }}>{acc.name}</span>
                <Label>{acc.type}</Label>
              </div>
              <span style={{ fontSize: 13, color: '#E8E9F2', fontFamily: 'Space Grotesk, sans-serif', fontWeight: 600 }}>{acc.val}</span>
            </div>
          ))}
        </div>

        <div style={{ height: 1, background: 'rgba(255,255,255,0.06)', margin: '4px 0' }} />

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <Label>July Spending</Label>
          {SPENDING_CATS.map((cat) => <SpendBar key={cat.label} {...cat} color={c} />)}
        </div>
      </div>

      {/* Center — orb */}
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        justifyContent: 'center', padding: '60px 32px', gap: 20,
      }}>
        <StateBadge state={state} />
        <div onClick={onOrbTap} style={{ cursor: 'pointer', userSelect: 'none' }}>
          <Orb state={state} size={280} />
        </div>
        <div key={state} style={{ textAlign: 'center', animation: 'fade-in 0.5s ease both', maxWidth: 260 }}>
          <p style={{
            fontSize: 14, color: state === 'idle' ? '#8B8FA8' : '#E8E9F2',
            fontFamily: 'Space Grotesk, sans-serif', fontWeight: 400,
            letterSpacing: '0.01em', lineHeight: 1.6, margin: 0, transition: 'color 0.5s ease',
          }}>{TRANSCRIPT[state]}</p>
        </div>
        <Waveform state={state} />
        <MicButton state={state} onTap={onOrbTap} />
      </div>

      {/* Right — Omega panel or activity */}
      <div style={{
        display: 'flex', flexDirection: 'column', justifyContent: 'flex-start',
        padding: '60px 64px 60px 48px', gap: 16,
        borderLeft: '1px solid rgba(255,255,255,0.05)',
        overflowY: 'auto',
      }}>
        {/* Toggle between activity and Omega */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
          <button
            onClick={() => !omegaOpen && onOmegaToggle()}
            style={{
              padding: '5px 12px', borderRadius: 999, border: 'none', cursor: 'pointer',
              background: !omegaOpen ? 'rgba(255,255,255,0.08)' : 'transparent',
              transition: 'background 0.3s ease',
            }}
          >
            <span style={{
              fontSize: 9, fontFamily: 'Space Grotesk, sans-serif', fontWeight: 500,
              letterSpacing: '0.35em', textTransform: 'uppercase',
              color: !omegaOpen ? '#E8E9F2' : '#8B8FA8',
            }}>Activity</span>
          </button>
          <OmegaHeaderBadge runningCount={omega.runningCount} open={omegaOpen} onToggle={onOmegaToggle} />
        </div>

        {omegaOpen ? (
          <OmegaPanel tasks={omega.tasks} onDispatch={omega.dispatch} />
        ) : (
          <>
            <Label>Recent Activity</Label>
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {TRANSACTIONS.map((tx, i) => <TxRow key={i} {...tx} />)}
            </div>

            <div style={{ marginTop: 16 }}>
              <Label>Insights from Fable</Label>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 12 }}>
                {[
                  { icon: '↑', color: COLOR.speaking, text: 'Dining spend is 23% over budget for July.' },
                  { icon: '↗', color: COLOR.listening, text: 'Savings rate improved 4 pts vs last month.' },
                  { icon: '→', color: COLOR.idle, text: "3 subscriptions you haven't used this month." },
                ].map((insight, i) => (
                  <div key={i} style={{
                    display: 'flex', gap: 12, alignItems: 'flex-start',
                    padding: '12px 14px', borderRadius: 14,
                    background: 'rgba(10,10,26,0.4)', border: '1px solid rgba(255,255,255,0.06)',
                  }}>
                    <span style={{ fontSize: 14, fontWeight: 700, color: insight.color, fontFamily: 'Space Grotesk, sans-serif', minWidth: 16 }}>{insight.icon}</span>
                    <span style={{ fontSize: 12, color: '#8B8FA8', fontFamily: 'Space Grotesk, sans-serif', lineHeight: 1.5, fontWeight: 400 }}>{insight.text}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Omega teaser */}
            <div style={{
              marginTop: 16, padding: '14px', borderRadius: 16,
              border: '1px solid rgba(138,92,255,0.2)',
              background: 'rgba(138,92,255,0.05)',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <span style={{ fontSize: 12, color: '#8A5CFF', fontFamily: 'Space Grotesk, sans-serif', fontWeight: 700 }}>Ω</span>
                <Label>Omega available</Label>
              </div>
              <p style={{ margin: 0, fontSize: 11, color: 'rgba(139,143,168,0.7)', fontFamily: 'Space Grotesk, sans-serif', lineHeight: 1.5 }}>
                Need deep research or a complex execution task? Route it to the Omega cluster.
              </p>
              <button
                onClick={onOmegaToggle}
                style={{
                  marginTop: 10, padding: '7px 14px', borderRadius: 999,
                  border: '1px solid rgba(138,92,255,0.35)',
                  background: 'rgba(138,92,255,0.12)', cursor: 'pointer',
                }}
              >
                <span style={{ fontSize: 10, fontFamily: 'Space Grotesk, sans-serif', letterSpacing: '0.25em', textTransform: 'uppercase', color: '#8A5CFF' }}>
                  Open Omega
                </span>
              </button>
            </div>
          </>
        )}

        <div style={{ marginTop: 'auto', paddingTop: 20, borderTop: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
            <div style={{
              width: 8, height: 8, borderRadius: '50%',
              background: c, boxShadow: `0 0 8px ${c}`,
              transition: 'background 0.6s ease, box-shadow 0.6s ease',
            }} />
            <Label>Fable is {LABEL[state].toLowerCase()}</Label>
          </div>
          <p style={{ fontSize: 11, color: 'rgba(139,143,168,0.5)', fontFamily: 'Space Grotesk, sans-serif', margin: 0, lineHeight: 1.6 }}>
            Your financial data is encrypted end-to-end.<br />Fable never stores voice recordings.
          </p>
        </div>
      </div>
    </div>
  )
}

// ─── Overlays ─────────────────────────────────────────────────────────────────

function GrainVignette() {
  return (
    <>
      <div style={{
        position: 'fixed', inset: 0, pointerEvents: 'none', zIndex: 100,
        opacity: 0.035, animation: 'grain 0.35s steps(1) infinite', mixBlendMode: 'overlay',
      }}>
        <svg width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">
          <filter id="grain-filter">
            <feTurbulence type="fractalNoise" baseFrequency="0.75" numOctaves="4" stitchTiles="stitch" />
            <feColorMatrix type="saturate" values="0" />
          </filter>
          <rect width="100%" height="100%" filter="url(#grain-filter)" />
        </svg>
      </div>
      <div style={{
        position: 'fixed', inset: 0, pointerEvents: 'none', zIndex: 99,
        background: 'radial-gradient(ellipse at 50% 48%, transparent 30%, rgba(2,2,8,0.55) 65%, rgba(1,1,6,0.88) 100%)',
      }} />
    </>
  )
}

function BackgroundGlow({ state }: { state: AppState }) {
  const c = COLOR[state]
  return (
    <div style={{
      position: 'fixed', inset: 0, pointerEvents: 'none', zIndex: 0,
      background: `
        radial-gradient(ellipse 70% 55% at 50% 42%, ${c}0D 0%, transparent 70%),
        radial-gradient(ellipse 100% 80% at 50% 42%, rgba(91,92,255,0.06) 0%, transparent 80%)
      `,
      transition: 'background 0.8s ease',
    }} />
  )
}

// ─── App ──────────────────────────────────────────────────────────────────────

export default function App() {
  const [state, setState] = useState<AppState>('idle')
  const [omegaOpen, setOmegaOpen] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const omega = useOmega()

  const clearTimer = useCallback(() => {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null }
  }, [])

  const advanceState = useCallback((current: AppState) => {
    const next = SEQ[(SEQ.indexOf(current) + 1) % SEQ.length]
    setState(next)
    if (next !== 'idle') {
      timerRef.current = setTimeout(() => advanceState(next), DURATIONS[next])
    }
  }, [])

  const handleOrbTap = useCallback(() => {
    clearTimer()
    if (state === 'idle') advanceState('idle')
    else setState('idle')
  }, [state, clearTimer, advanceState])

  const handleOmegaToggle = useCallback(() => setOmegaOpen((o) => !o), [])

  useEffect(() => () => clearTimer(), [clearTimer])

  return (
    <div style={{ position: 'relative', minHeight: '100dvh', background: '#05050D', overflow: 'hidden' }}>
      <BackgroundGlow state={state} />
      <GrainVignette />
      <div style={{ position: 'relative', zIndex: 1 }}>
        <div style={{ display: 'flex', justifyContent: 'center' }} className="block lg:hidden">
          <MobileView state={state} onOrbTap={handleOrbTap} omega={omega} omegaOpen={omegaOpen} onOmegaToggle={handleOmegaToggle} />
        </div>
        <div className="hidden lg:block" style={{ minHeight: '100dvh' }}>
          <DesktopView state={state} onOrbTap={handleOrbTap} omega={omega} omegaOpen={omegaOpen} onOmegaToggle={handleOmegaToggle} />
        </div>
      </div>
    </div>
  )
}
