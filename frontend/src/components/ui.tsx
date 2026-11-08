/** Shared components, UI_SPEC section 2. Colors come only from the tailwind tokens. */
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import type { CoreState } from '../api/types'

const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(' ')

// ---------- badge ----------
export type BadgeTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'primary' | 'blue'

const BADGE: Record<BadgeTone, string> = {
  success: 'bg-success-bg text-success-text',
  warning: 'bg-warning-bg text-warning-text',
  danger: 'bg-danger-bg text-danger-text',
  info: 'bg-info-bg text-accent-blue-text',
  neutral: 'bg-page text-muted',
  primary: 'bg-primary-soft text-primary',
  blue: 'bg-accent-blue-bg text-accent-blue-text',
}

export function Badge({ tone = 'neutral', children, className }: { tone?: BadgeTone; children: ReactNode; className?: string }) {
  return (
    <span className={cx('inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[13px] font-medium', BADGE[tone], className)}>
      {children}
    </span>
  )
}

// ---------- course status pill ----------
const STATUS: Record<CoreState, { label: string; tone: BadgeTone }> = {
  completed: { label: 'Completed', tone: 'success' },
  in_progress: { label: 'In progress', tone: 'warning' },
  waived: { label: 'Waived', tone: 'blue' },
  remaining: { label: 'Remaining', tone: 'neutral' },
}

export function StatusPill({ status }: { status: CoreState }) {
  const s = STATUS[status]
  return <Badge tone={s.tone}>{s.label}</Badge>
}

export function LockIcon({ className }: { className?: string }) {
  return (
    <svg className={cx('h-3.5 w-3.5', className)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </svg>
  )
}

/** "Required" with a lock icon: locked core course in a draft schedule (used from M7b). */
export function RequiredPill() {
  return (
    <span className="inline-flex items-center gap-1 text-[13px] text-muted">
      <LockIcon /> Required
    </span>
  )
}

// ---------- buttons ----------
type ButtonVariant = 'primary' | 'outline' | 'danger' | 'ghost'
const BTN: Record<ButtonVariant, string> = {
  primary: 'bg-primary text-white hover:bg-primary-dark disabled:opacity-50',
  outline: 'border border-border bg-card text-ink hover:bg-page disabled:opacity-50',
  danger: 'border border-danger-text bg-card text-danger-text hover:bg-danger-bg disabled:opacity-50',
  ghost: 'text-primary hover:bg-primary-soft disabled:opacity-50',
}

export function Button({
  variant = 'primary',
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      {...props}
      className={cx('h-11 rounded-control px-5 text-[15px] font-medium transition-colors', BTN[variant], className)}
    />
  )
}

// ---------- cards ----------
export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={cx('rounded-card border border-border bg-card p-6', className)}>{children}</section>
}

export function CardTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-4 flex items-baseline justify-between gap-4">
      <h2 className="text-[20px] font-semibold">{children}</h2>
      {aside && <div className="text-[14px] text-muted">{aside}</div>}
    </div>
  )
}

export function InfoBanner({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex items-center gap-4 rounded-card border border-info-border bg-info-bg px-5 py-4 text-[15px]">
      <svg className="h-5 w-5 shrink-0 text-accent-blue-text" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v5M12 16.5v.01" strokeLinecap="round" />
      </svg>
      <p className="flex-1">{children}</p>
      {action}
    </div>
  )
}

// ---------- course row (M7b uses all parts; the dashboard uses code, title, units, right) ----------
export function CourseRow({
  code,
  title,
  meta,
  note,
  units,
  right,
}: {
  code: string
  title: string
  meta?: ReactNode
  note?: ReactNode
  units?: string
  right?: ReactNode
}) {
  return (
    <div className="flex items-start gap-4 border-b border-border py-3.5 last:border-b-0">
      <span className="w-16 shrink-0 pt-px font-mono text-[14px] font-medium">{code}</span>
      <div className="min-w-0 flex-1">
        <div>{title}</div>
        {meta && <div className="text-[13px] text-muted">{meta}</div>}
        {note && <div className="text-[13px] text-accent-blue-text">{note}</div>}
      </div>
      {units && <span className="shrink-0 pt-px text-[14px] text-muted">{units}</span>}
      {right && <div className="flex w-28 shrink-0 justify-end">{right}</div>}
    </div>
  )
}

// ---------- match bar ----------
export function MatchBar({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(1, score)) * 100
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-border">
        <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-[13px] text-muted">{score.toFixed(2)}</span>
    </div>
  )
}

// ---------- unit meter ----------
export function UnitMeter({ units, limit, hint }: { units: number; limit: number; hint?: ReactNode }) {
  const pct = Math.min(100, (units / limit) * 100)
  return (
    <div>
      <div className="flex items-baseline justify-between gap-4">
        <span className="font-medium">
          {units} of {limit} units this term
        </span>
        {hint && <span className="text-[13px] text-muted">{hint}</span>}
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-border">
        <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// ---------- loading, error, empty ----------
export function Skeleton({ className }: { className?: string }) {
  return <div className={cx('animate-pulse rounded-control bg-border', className)} />
}

export function PageSkeleton({ message }: { message?: string }) {
  return (
    <div role="status" aria-live="polite" className="space-y-6">
      <Skeleton className="h-10 w-72" />
      <Skeleton className="h-5 w-96" />
      <Skeleton className="h-16 w-full" />
      <div className="grid grid-cols-[62fr_38fr] gap-6">
        <Skeleton className="h-64" />
        <Skeleton className="h-64" />
      </div>
      {message && <p className="text-muted">{message}</p>}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-card border border-danger-text/30 bg-danger-bg p-5 text-danger-text">
      <p className="font-medium">Something went wrong</p>
      <p className="mt-1 text-[14px]">{message}</p>
      {onRetry && (
        <Button variant="danger" className="mt-3 h-10" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="rounded-control border border-dashed border-border px-4 py-3 text-muted">{children}</p>
}
