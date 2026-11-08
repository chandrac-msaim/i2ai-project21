import { useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import {
  num,
  type AdvisorScheduleView,
  type DecisionResult,
  type EmailPreview,
  type FlagView,
  type MessageView,
  type Recheck,
  type ScheduleCheck,
  type ScheduleItem,
} from '../api/types'
import { useGet } from '../api/useApi'
import { useAuth } from '../auth'
import { AdvisorTopBar, PageShell } from '../components/TopBar'
import { Badge, Button, Card, CardTitle, ErrorState, PageSkeleton } from '../components/ui'
import { modalityLabel, seatsLabel, unitsLabel } from '../lib/format'
import { FLAG_LABEL } from './AdvisorCaseload'

const enc = encodeURIComponent
const strip = (s: string) => s.replace(/^[A-Z_]+( [\w-]+)?: /, '')
const CHECK_LABEL: Record<string, string> = {
  UNIT_LIMIT: 'Unit limit',
  PREREQ: 'Prerequisites',
  OFFERED: 'Offered this term, seats open',
  LEVEL: 'Course level',
  APPROVAL: 'Special approval',
  TIME_CONFLICT: 'Time conflicts',
  PRIVACY: 'Data and privacy',
}
const STATUS_CHIP: Record<string, { label: string; tone: 'warning' | 'success' | 'danger' | 'neutral' }> = {
  submitted: { label: 'Schedule awaiting your review', tone: 'warning' },
  approved: { label: 'Schedule approved', tone: 'success' },
  rejected: { label: 'Schedule rejected', tone: 'danger' },
  draft: { label: 'Student is still editing', tone: 'neutral' },
}

export default function AdvisorReview() {
  const { id = '' } = useParams()
  const { user } = useAuth()
  const { data, error, status, loading, reload } = useGet<AdvisorScheduleView>(`/advisor/schedules/${enc(id)}`)
  const forbidden = status === 403

  const top = <AdvisorTopBar name={data?.student.advisor_name ?? undefined} />
  if (loading && !data) return <PageShell top={top}><PageSkeleton /></PageShell>
  if (error && !data)
    return (
      <PageShell top={top}>
        <Back />
        {forbidden ? (
          <Card className="mt-6 max-w-xl">
            <h1 className="text-[24px] font-semibold">This student is not in your caseload</h1>
            <p className="mt-2 text-muted">Only a student&apos;s own advisor can open and decide their schedule. {error}</p>
            <Link to="/advisor" className="mt-4 inline-block text-primary underline">Back to my caseload</Link>
          </Card>
        ) : (
          <div className="mt-6"><ErrorState message={error} onRetry={reload} /></div>
        )}
      </PageShell>
    )
  if (!data) return null

  const readOnly = user?.role !== 'advisor'
  return (
    <PageShell top={top}>
      <Back name={`${data.student.first_name} ${data.student.last_name}`} />
      <Body d={data} readOnly={readOnly} reload={reload} />
    </PageShell>
  )
}

function Back({ name }: { name?: string }) {
  return (
    <p className="text-[15px] text-muted">
      <Link to="/advisor" className="text-primary underline">Caseload</Link>
      {name && <> / {name}</>}
    </p>
  )
}

function Body({ d, readOnly, reload }: { d: AdvisorScheduleView; readOnly: boolean; reload: () => void }) {
  const s = d.schedule
  const st = d.student
  const chip = STATUS_CHIP[s.status] ?? { label: s.status, tone: 'neutral' as const }
  const passed = s.checks.filter((c) => c.passed).length
  const [recheck, setRecheck] = useState<{ at: Date; result: Recheck | null }>({ at: new Date(), result: null })

  return (
    <div className="mt-6 space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-[40px] font-semibold leading-tight">{st.first_name} {st.last_name}</h1>
          <p className="mt-1 text-[17px] text-muted">
            <span className="font-mono">{st.student_id}</span> · {st.university_name} · {st.program.college} · {st.program.title} ·{' '}
            {s.term_label} · QPA{' '}
            <span className={st.below_min_qpa ? 'font-semibold text-danger-text' : ''}>{st.qpa == null ? '-' : num(st.qpa).toFixed(2)}</span> ·{' '}
            {num(st.units_completed)} units done
          </p>
        </div>
        <Badge tone={chip.tone} className="px-4 py-1.5">{chip.label}</Badge>
      </div>

      {readOnly && (
        <div className="rounded-card border border-info-border bg-info-bg px-5 py-3 text-[15px]">
          You are viewing this as an administrator. Only the student&apos;s own advisor can approve or reject.
        </div>
      )}

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[2fr_3fr]">
        <div className="space-y-6">
          <ChecksCard checks={s.checks} passed={passed} />
          <FlagsCard flags={s.flags} />
          <AiCard d={d} />
        </div>
        <div className="space-y-6">
          <ScheduleCard d={d} state={recheck} setState={setRecheck} canRecheck={!readOnly && s.status === 'submitted'} />
          <MessagesCard d={d} readOnly={readOnly} />
          <DecisionCard d={d} readOnly={readOnly} onDecided={reload} />
        </div>
      </div>
    </div>
  )
}

// ---------- left column ----------
function ChecksCard({ checks, passed }: { checks: ScheduleCheck[]; passed: number }) {
  return (
    <Card>
      <CardTitle aside={checks.length > 0 && <Badge tone={passed === checks.length ? 'success' : 'warning'}>{passed} of {checks.length} passed</Badge>}>
        Checks and compliance
      </CardTitle>
      {checks.length === 0 && <p className="text-muted">No live checks: this schedule is no longer open.</p>}
      <ul className="divide-y divide-border">
        {checks.map((c) => (
          <li key={c.code} className="flex gap-3 py-3">
            <span className={c.passed ? 'text-success-text' : c.advisor_flag ? 'text-warning-text' : 'text-danger-text'} aria-hidden>
              {c.passed ? '✓' : c.advisor_flag ? '!' : '✗'}
            </span>
            <div>
              <p className="font-semibold">{CHECK_LABEL[c.code] ?? c.code}</p>
              <p className="text-[14px] text-muted">{c.detail}</p>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function FlagsCard({ flags }: { flags: FlagView[] }) {
  return (
    <Card>
      <CardTitle>Risk flags</CardTitle>
      {flags.length === 0 && <Badge tone="success">No risk flags</Badge>}
      <div className="space-y-3">
        {flags.map((f) => {
          const label = FLAG_LABEL[f.flag_code]?.label ?? f.flag_code
          const border = f.severity === 'high' ? 'border-danger-text/40' : f.severity === 'medium' ? 'border-warning-text/40' : 'border-border'
          return (
            <div key={f.flag_id} className={`rounded-card border p-4 ${border}`}>
              <div className="flex items-center gap-3">
                <Badge tone={f.severity === 'high' ? 'danger' : f.severity === 'medium' ? 'warning' : 'neutral'}>
                  {f.severity.charAt(0).toUpperCase() + f.severity.slice(1)}
                </Badge>
                <span className="font-semibold">{label}</span>
              </div>
              <p className="mt-2">{f.message}</p>
              {f.rule_text && <p className="mt-1 text-[13px] text-muted">Rule: {f.rule_text}</p>}
              {f.suggested_action && <p className="mt-1 text-[13px] text-muted">Suggested: {f.suggested_action}</p>}
            </div>
          )
        })}
      </div>
    </Card>
  )
}

function AiCard({ d }: { d: AdvisorScheduleView }) {
  const run = d.ai_run
  return (
    <Card>
      <CardTitle>How the AI built this</CardTitle>
      <p className="whitespace-pre-line">{d.ai_explanation || 'No explanation was saved for this schedule.'}</p>
      {d.removed_from_ai_draft.length > 0 && (
        <p className="mt-3 text-[14px]">
          <strong>Left out by the student:</strong> {d.removed_from_ai_draft.map((c) => `${c.course_code} ${c.title}`).join('; ')}
        </p>
      )}
      {run && (
        <p className="mt-3 text-[13px] text-muted">
          Agent {run.agent_name} · {run.llm_provider ? `${run.llm_provider} ${run.llm_model ?? ''}` : 'template text (no LLM)'} · tools: {run.tools.join(', ') || 'none'}
        </p>
      )}
    </Card>
  )
}

// ---------- right column ----------
function ago(at: Date, now = new Date()): string {
  const min = Math.floor((now.getTime() - at.getTime()) / 60000)
  return min < 1 ? 'just now' : min === 1 ? '1 minute ago' : `${min} minutes ago`
}

function ScheduleCard({ d, state, setState, canRecheck }: { d: AdvisorScheduleView; state: { at: Date; result: Recheck | null }; setState: (s: { at: Date; result: Recheck | null }) => void; canRecheck: boolean }) {
  const s = d.schedule
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const titles = new Map(d.removed_from_ai_draft.map((c) => [c.course_code, c.title]))
  d.changes.forEach((c) => c.replaced_course_code && c.replaced_course_title && titles.set(c.replaced_course_code, c.replaced_course_title))
  const total = num(s.total_units)
  const extended = total > s.max_units
  const lowSeats = s.items.filter((i) => i.seats_left != null && i.seats_left <= 3)
  const blocking = state.result?.blocking ?? s.blocking

  async function run() {
    setBusy(true)
    setErr(null)
    try {
      const result = await api.post<Recheck>(`/advisor/schedules/${s.schedule_id}/recheck`)
      setState({ at: new Date(), result })
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Could not re-check')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="p-6">
      <CardTitle aside={`${unitsLabel(total)} · ${s.term_label} · version ${s.version}`}>{d.student.first_name}&apos;s schedule</CardTitle>
      {s.status !== 'draft' && s.checks.length > 0 && (
        <div className={`mb-4 flex flex-wrap items-center justify-between gap-3 rounded-card border px-4 py-3 text-[14px] ${blocking.length ? 'border-danger-text/30 bg-danger-bg' : 'border-success-text/20 bg-success-bg'}`}>
          <p>
            <strong>Seats and availability re-checked {ago(state.at)}.</strong>{' '}
            {blocking.length ? blocking.map(strip).join(' ') : `All ${s.items.length} sections are open.`}
            {lowSeats.map((i) => ` ${i.course_code} has only ${i.seats_left} ${i.seats_left === 1 ? 'seat' : 'seats'} left.`).join('')}
          </p>
          {canRecheck && <Button variant="outline" className="h-10" disabled={busy} onClick={() => void run()}>{busy ? 'Checking…' : 'Check again'}</Button>}
        </div>
      )}
      {err && <p role="alert" className="mb-3 text-danger-text">{err}</p>}

      {s.items.map((i) => <Row key={i.course_code} i={i} original={i.ai_original_course ? titles.get(i.ai_original_course) : undefined} />)}

      <div className="mt-4 rounded-card bg-page p-4 text-[15px]">
        <p><strong>Units this term:</strong> {d.load_rationale || `${unitsLabel(total)}.`}</p>
        {extended && <p className="mt-1 text-warning-text">Extended load: {total} units is above {s.max_units}. {s.extended_allowed ? `Allowed because the QPA is above ${num(s.extended_units_min_qpa).toFixed(2)}.` : 'Not allowed for this QPA.'}</p>}
      </div>
    </Card>
  )
}

function Row({ i, original }: { i: ScheduleItem; original?: string }) {
  const changed = i.added_by === 'student' && i.item_type === 'elective'
  const meta = [i.days_times, modalityLabel(i.modality), i.seats_left != null ? seatsLabel(i.seats_left) : null].filter(Boolean).join(' · ')
  return (
    <div className={`flex items-start gap-4 border-b border-border px-2 py-3.5 last:border-b-0 ${changed ? 'bg-changed-bg' : ''}`}>
      <span className="w-16 shrink-0 pt-px font-mono text-[14px] font-medium">{i.course_code}</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          {i.title}
          {changed && <Badge tone="warning">Changed by student</Badge>}
          {i.is_conditional && <Badge tone="blue">Conditional</Badge>}
          {i.needs_approval && <Badge tone="info">Needs approval</Badge>}
        </div>
        <p className="text-[13px] text-muted">
          {meta}
          {i.ai_original_course && (
            <>
              {' '}· AI suggested <s>{i.ai_original_course}{original ? ` ${original}` : ''}</s>
            </>
          )}
          {changed && !i.ai_original_course && ' · added by the student'}
        </p>
      </div>
      <Badge tone={i.item_type === 'core' ? 'primary' : 'info'}>{i.item_type === 'core' ? 'Core' : 'Elective'}</Badge>
      <span className="w-16 shrink-0 text-right text-[14px] text-muted">{unitsLabel(num(i.units))}</span>
    </div>
  )
}

function MessagesCard({ d, readOnly }: { d: AdvisorScheduleView; readOnly: boolean }) {
  const [messages, setMessages] = useState<MessageView[]>(d.messages)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const unread = d.messages.filter((m) => !m.mine && !m.read_at).length

  async function send() {
    if (!text.trim()) return
    setBusy(true)
    setErr(null)
    try {
      const m = await api.post<MessageView>(`/messages/${d.student.student_id}`, { body: text.trim(), schedule_id: d.schedule.schedule_id })
      setMessages((all) => [...all, m])
      setText('')
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Could not send')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="p-0">
      <div className="flex items-center justify-between border-b border-border px-6 py-4">
        <h2 className="text-[20px] font-semibold">Messages with {d.student.first_name}</h2>
        {unread > 0 && <Badge tone="primary">{unread} new</Badge>}
      </div>
      <div className="space-y-4 px-6 py-5">
        {messages.length === 0 && <p className="text-muted">No messages yet.</p>}
        {messages.map((m) => (
          <div key={m.message_id} className={m.mine ? 'text-right' : ''}>
            <div className={`inline-block max-w-[85%] whitespace-pre-line rounded-2xl px-4 py-3 text-left ${m.mine ? 'bg-primary text-white' : 'bg-page'}`}>{m.body}</div>
            <p className="mt-1 text-[12px] text-muted">
              {m.mine ? 'You' : m.sender_name} · {new Date(m.sent_at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}
              {!m.mine && !m.read_at && <span className="font-semibold text-primary"> · New</span>}
            </p>
          </div>
        ))}
      </div>
      {!readOnly && (
        <div className="border-t border-border px-6 py-4">
          {err && <p role="alert" className="mb-2 text-danger-text">{err}</p>}
          <div className="flex gap-3">
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void send()}
              placeholder={`Write a message to ${d.student.first_name}`}
              aria-label={`Message to ${d.student.first_name}`}
              className="h-11 flex-1 rounded-control border border-border px-3"
            />
            <Button variant="outline" disabled={busy || !text.trim()} onClick={() => void send()}>Send</Button>
          </div>
        </div>
      )}
    </Card>
  )
}

// ---------- the decision ----------
function DecisionCard({ d, readOnly, onDecided }: { d: AdvisorScheduleView; readOnly: boolean; onDecided: () => void }) {
  const s = d.schedule
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<{ title: string; reasons: string[] } | null>(null)
  const [done, setDone] = useState<DecisionResult | null>(null)
  const [preview, setPreview] = useState<EmailPreview | null>(null)
  const [previewing, setPreviewing] = useState<'approve' | 'reject' | null>(null)

  if (s.status !== 'submitted' && !done) {
    return (
      <Card>
        <CardTitle>Your decision</CardTitle>
        <p className="text-muted">
          {s.status === 'draft' ? 'The student has not sent this schedule yet.' : `This schedule is ${s.status}. No decision is needed.`}
        </p>
        {s.advisor_comment && <p className="mt-3 rounded-control bg-page px-4 py-3"><strong>Your comment:</strong> {s.advisor_comment}</p>}
      </Card>
    )
  }

  async function decide(decision: 'approve' | 'reject') {
    if (decision === 'reject' && !comment.trim()) {
      setProblem({ title: 'Write a comment first: the student needs to know why the schedule was rejected.', reasons: [] })
      return
    }
    setBusy(true)
    setProblem(null)
    try {
      setDone(await api.post<DecisionResult>(`/advisor/schedules/${s.schedule_id}/decision`, { decision, comment: comment.trim() || null }))
      onDecided()
    } catch (e) {
      if (e instanceof ApiError) {
        const reasons = Array.isArray(e.body.reasons) ? (e.body.reasons as string[]).map(strip) : []
        setProblem({ title: e.code === 'compliance_blocked' ? 'Cannot approve right now. The schedule stays submitted.' : e.message, reasons })
      } else setProblem({ title: 'Could not save the decision', reasons: [] })
    } finally {
      setBusy(false)
    }
  }

  async function openPreview(decision: 'approve' | 'reject') {
    setPreviewing(decision)
    setProblem(null)
    try {
      setPreview(await api.post<EmailPreview>(`/advisor/schedules/${s.schedule_id}/email-preview`, { decision, comment: comment.trim() || null }))
    } catch (e) {
      setProblem({ title: e instanceof ApiError ? e.message : 'Could not build the preview', reasons: [] })
    } finally {
      setPreviewing(null)
    }
  }

  if (done) {
    const approved = done.status === 'approved'
    return (
      <Card>
        <CardTitle>Your decision</CardTitle>
        <div role="status" className={`rounded-control px-4 py-3 ${approved ? 'bg-success-bg text-success-text' : 'bg-danger-bg text-danger-text'}`}>
          <p className="font-semibold">{approved ? 'Schedule approved' : 'Schedule rejected'}</p>
          <p className="mt-1 text-[14px]">
            {done.email_sent ? `Email sent to ${d.student.first_name}: ${done.email_subject}.` : `The decision is saved, but the email was not sent${done.email_error ? ` (${done.email_error})` : ''}.`}
          </p>
        </div>
      </Card>
    )
  }

  return (
    <Card>
      <CardTitle>Your decision</CardTitle>
      {readOnly ? (
        <p className="text-muted">Read only. Only the student&apos;s own advisor can decide.</p>
      ) : (
        <>
          <label className="font-semibold" htmlFor="comment">Comment to the student</label>
          <textarea
            id="comment"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={4}
            placeholder="For example: approved; let's meet in week 3 to check on progress."
            className="mt-2 w-full rounded-control border border-border p-3"
          />
          {problem && (
            <div role="alert" className="mt-3 rounded-control border border-danger-text/30 bg-danger-bg px-4 py-3 text-[14px] text-danger-text">
              <p className="font-medium">{problem.title}</p>
              {problem.reasons.length > 0 && <ul className="mt-1 list-disc pl-5">{problem.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
            </div>
          )}
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button disabled={busy} onClick={() => void decide('approve')}>{busy ? 'Saving…' : 'Approve schedule'}</Button>
            <Button variant="danger" disabled={busy} onClick={() => void decide('reject')}>Reject with comment</Button>
            <button className="text-primary underline" disabled={previewing !== null} onClick={() => void openPreview('approve')}>
              {previewing ? 'Loading…' : 'Preview email'}
            </button>
          </div>
          {busy && <p role="status" className="mt-3 text-[14px] text-muted">Saving your decision and writing the email… this can take up to a minute</p>}
          <p className="mt-4 text-[13px] text-muted">
            Seats are checked again when you click Approve. {d.student.first_name} gets an email with your decision and comment. Only an advisor can
            approve a schedule; the AI cannot. Your decision is recorded in the audit log.
          </p>
        </>
      )}
      {preview && <PreviewModal p={preview} onClose={() => setPreview(null)} switchTo={(x) => void openPreview(x)} />}
    </Card>
  )
}

function PreviewModal({ p, onClose, switchTo }: { p: EmailPreview; onClose: () => void; switchTo: (d: 'approve' | 'reject') => void }): ReactNode {
  const [plain, setPlain] = useState(false)
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-ink/40 p-4" role="dialog" aria-modal="true" aria-label="Email preview">
      <div className="flex max-h-[92vh] w-full max-w-3xl flex-col rounded-card bg-card">
        <div className="flex items-center justify-between gap-3 border-b border-border px-6 py-4">
          <div>
            <h2 className="text-[20px] font-semibold">Email preview</h2>
            <p className="text-[13px] text-muted">{p.note} Showing the {p.subject.includes('needs changes') ? 'rejection' : 'approval'} email.</p>
            <p className="mt-1 text-[13px]"><strong>Subject:</strong> {p.subject}<br /><strong>To:</strong> {p.to}<br /><strong>Cc:</strong> {p.cc ?? '-'}</p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" className="h-10" onClick={() => setPlain((v) => !v)}>{plain ? 'Show HTML' : 'Show plain text'}</Button>
            <Button variant="outline" className="h-10" onClick={() => switchTo('approve')}>Approval</Button>
            <Button variant="outline" className="h-10" onClick={() => switchTo('reject')}>Rejection</Button>
            <Button className="h-10" onClick={onClose}>Close</Button>
          </div>
        </div>
        {plain ? (
          <pre className="h-[70vh] w-full flex-1 overflow-auto whitespace-pre-wrap rounded-b-card p-6 font-sans text-[14px]">{p.text}</pre>
        ) : (
          <iframe title="Email preview" srcDoc={p.html} sandbox="" className="h-[70vh] w-full flex-1 rounded-b-card" />
        )}
      </div>
    </div>
  )
}
