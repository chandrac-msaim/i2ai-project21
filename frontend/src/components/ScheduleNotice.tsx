/** The one notice on the student schedule page. Its text, colour and buttons depend only on the schedule status,
 *  so a locked schedule never says "This is a draft" (docs/UI_SPEC.md section 3, M8e). */
import type { ReactNode } from 'react'
import type { ScheduleView } from '../api/types'
import { fmtDate } from '../lib/format'
import { Button } from './ui'

export type ScheduleStatus = ScheduleView['status']

export interface ScheduleNoticeProps {
  status: ScheduleStatus
  advisorName?: string | null
  submittedAt?: string | null
  decidedAt?: string | null
  advisorComment?: string | null
  canResubmit?: boolean // rejected schedule that can become a new draft
  busy?: boolean
  onResubmit?: () => void
  onBack?: (() => void) | null // set when an older version is open: back to the current schedule
}

const TONE: Record<ScheduleStatus, string> = {
  draft: 'border-warning-text/20 bg-warning-bg text-warning-text',
  submitted: 'border-info-border bg-info-bg text-accent-blue-text',
  approved: 'border-success-text/20 bg-success-bg text-success-text',
  rejected: 'border-danger-text/30 bg-danger-bg text-danger-text',
  discarded: 'border-border bg-page text-muted',
  superseded: 'border-border bg-page text-muted',
}

const on = (iso?: string | null) => (iso ? ` on ${fmtDate(iso.slice(0, 10))}` : '')
const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

export default function ScheduleNotice(p: ScheduleNoticeProps) {
  const who = p.advisorName || 'your advisor'
  const comment = (label: string, fallback?: string): ReactNode => {
    const text = p.advisorComment?.trim() || fallback
    return text ? (
      <p className="mt-2 rounded-control bg-card/60 px-3 py-2 text-[14px] text-ink">
        <strong>{label}</strong> {text}
      </p>
    ) : null
  }

  let body: ReactNode
  switch (p.status) {
    case 'draft':
      body = <p>This is a draft. {capital(who)} approves the final schedule and sees which changes you made.</p>
      break
    case 'submitted':
      body = (
        <p>
          Sent to {who}
          {on(p.submittedAt)}. You can withdraw until a decision is made.
        </p>
      )
      break
    case 'approved':
      body = (
        <>
          <p className="font-medium">
            Approved by {who}
            {on(p.decidedAt)}. This schedule is locked.
          </p>
          {comment('Comment from your advisor:')}
        </>
      )
      break
    case 'rejected':
      body = (
        <>
          <p className="font-medium">
            Not approved by {who}
            {on(p.decidedAt)}.
          </p>
          {comment('Comment from your advisor:', 'No comment was left.')}
          {p.canResubmit && (
            <Button className="mt-3" variant="danger" disabled={p.busy} onClick={p.onResubmit}>
              Edit and resubmit
            </Button>
          )}
        </>
      )
      break
    case 'discarded':
      body = <p>You discarded this draft. It stays in your history and cannot be changed.</p>
      break
    default:
      body = <p>A newer version replaced this one. It stays in your history and cannot be changed.</p>
  }

  return (
    <div role="status" data-testid="schedule-notice" data-status={p.status} className={`rounded-card border px-5 py-4 ${TONE[p.status]}`}>
      {body}
      {p.onBack && (
        <button type="button" className="mt-3 text-[14px] underline" onClick={p.onBack}>
          Back to the current schedule
        </button>
      )}
    </div>
  )
}
