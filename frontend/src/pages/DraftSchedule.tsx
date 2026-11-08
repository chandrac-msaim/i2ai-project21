import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import {
  num,
  type CourseFull,
  type CourseHit,
  type Dashboard,
  type HistoryItem,
  type ScheduleItem,
  type ScheduleView,
  type Suggestion,
  type SuggestionGroups,
} from '../api/types'
import { useGet } from '../api/useApi'
import { useAuth } from '../auth'
import StudentMessages from '../components/StudentMessages'
import ScheduleNotice from '../components/ScheduleNotice'
import { PageShell, StudentTopBar } from '../components/TopBar'
import { Badge, Button, Card, CardTitle, EmptyState, ErrorState, InfoBanner, MatchBar, PageSkeleton, RequiredPill, UnitMeter } from '../components/ui'
import { fmtDate, modalityLabel, seatsLabel, unitsLabel } from '../lib/format'

const enc = encodeURIComponent
const strip = (s: string) => s.replace(/^[A-Z_]+( [\w-]+)?: /, '') // "UNIT_LIMIT: text" -> "text"
const humanCode = (c: string) => c.charAt(0) + c.slice(1).toLowerCase().replace(/_/g, ' ')

/** Notice shown above the draft: a refused change (with the rule's reason) or a plain error. */
interface Notice {
  tone: 'danger' | 'info'
  title: string
  reasons: string[]
}

function toNotice(e: unknown, fallback: string): Notice {
  if (e instanceof ApiError) {
    const reasons = Array.isArray(e.body.reasons) ? (e.body.reasons as string[]).map(strip) : []
    if (e.code === 'edit_blocked') return { tone: 'danger', title: 'That change is not allowed, your draft is unchanged.', reasons }
    if (e.code === 'compliance_blocked') return { tone: 'danger', title: 'Your schedule cannot be sent yet.', reasons }
    return { tone: 'danger', title: e.message, reasons: [] }
  }
  return { tone: 'danger', title: fallback, reasons: [] }
}

// A build takes minutes. The page can be left meanwhile (the server keeps building), so the "building" flag lives in
// sessionStorage, not in component state: coming back shows "Building…" and polls instead of offering a second build.
const BUILD_MAX_MS = 10 * 60 * 1000
const buildKey = (term: string) => `graydi_building_${term}`

function readBuild(term: string): { v: number } | null {
  try {
    const raw = sessionStorage.getItem(buildKey(term))
    if (!raw) return null
    const f = JSON.parse(raw) as { t: number; v: number }
    return Date.now() - f.t < BUILD_MAX_MS ? { v: f.v } : null
  } catch {
    return null
  }
}
const markBuild = (term: string, version: number) => {
  try {
    sessionStorage.setItem(buildKey(term), JSON.stringify({ t: Date.now(), v: version }))
  } catch {
    /* storage blocked: the page then behaves as before */
  }
}
const clearBuild = (term: string) => {
  try {
    sessionStorage.removeItem(buildKey(term))
  } catch {
    /* ignore */
  }
}

type Confirm = { title: string; body: string; action: string; danger?: boolean; run: () => Promise<void> } | null

export default function DraftSchedule() {
  const { term = '' } = useParams()
  const dash = useGet<Dashboard>('/students/me/dashboard')
  const cur = useGet<ScheduleView | null>(`/schedules/current?term=${enc(term)}`)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [confirm, setConfirm] = useState<Confirm>(null)
  const [submitChecks, setSubmitChecks] = useState<ScheduleView | null>(null)

  const current = cur.data
  const [viewId, setViewId] = useState<string | null>(null) // an older version opened from "Previous versions"
  const [viewed, setViewed] = useState<ScheduleView | null>(null)
  useEffect(() => {
    if (!viewId) {
      setViewed(null)
      return
    }
    let cancelled = false
    api
      .get<ScheduleView>(`/schedules/${viewId}`)
      .then((v) => !cancelled && setViewed(v))
      .catch(() => !cancelled && setViewId(null))
    return () => {
      cancelled = true
    }
  }, [viewId])
  const viewingOld = viewed !== null && viewed.schedule_id !== current?.schedule_id
  const schedule = viewingOld ? viewed : current
  const d = dash.data
  const termInfo = d && [...d.terms.plannable, ...d.terms.upcoming, ...d.terms.recent_closed].find((t) => t.term_code === term)
  const termLabel = schedule?.term_label ?? termInfo?.term_label ?? '' // from the API, never built here
  const name = d ? `${d.student.first_name} ${d.student.last_name}` : undefined

  /** Run one API call that returns the new schedule view; show a refusal as a notice instead of changing anything. */
  const act = useCallback(
    async (call: () => Promise<ScheduleView | undefined>, fallback = 'Something went wrong') => {
      setBusy(true)
      setNotice(null)
      try {
        const view = await call()
        if (view) cur.setData(view)
        else cur.reload()
        return view
      } catch (e) {
        setNotice(toNotice(e, fallback))
        return undefined
      } finally {
        setBusy(false)
      }
    },
    [cur],
  )

  const [building, setBuilding] = useState(() => readBuild(term) !== null)

  const build = (replace: boolean) => {
    markBuild(term, current?.version ?? 0)
    setBuilding(true)
    return act(() => api.post<ScheduleView>('/schedules/build', { term_code: term, replace }), 'Could not build the schedule').finally(() => {
      clearBuild(term) // runs even if the page was left and reopened meanwhile
      setBuilding(false)
    })
  }

  // Returned to the page while a build is still running on the server: poll until a newer version exists.
  useEffect(() => {
    if (!building) return
    const id = setInterval(() => {
      const flag = readBuild(term)
      if (!flag) {
        setBuilding(false)
        cur.reload()
        return
      }
      api
        .get<ScheduleView | null>(`/schedules/current?term=${enc(term)}`)
        .then((view) => {
          if (view && view.version > flag.v) {
            clearBuild(term)
            setBuilding(false)
            cur.setData(view)
          }
        })
        .catch(() => undefined)
    }, 4000)
    return () => clearInterval(id)
  }, [building, term, cur])

  if (cur.loading && !schedule && !cur.error) return shell(name, term, <PageSkeleton message="Loading your schedule…" />)
  if (cur.error) return shell(name, term, <ErrorState message={cur.error} onRetry={cur.reload} />)

  return shell(
    name,
    term,
    <div className="space-y-6">
      <div>
        <h1 className="text-[36px] font-semibold leading-tight">Your{termLabel ? ` ${termLabel}` : ''} schedule</h1>
        <p className="mt-1 text-[17px] text-muted">
          {!schedule || schedule.status === 'draft'
            ? 'The AI built one schedule from your remaining requirements and saved interests. Add, swap or remove electives. Every change is checked again.'
            : schedule.status === 'rejected'
              ? 'Your advisor asked for changes. Use "Edit and resubmit" to continue.'
              : 'This schedule is read only.'}
        </p>
      </div>

      {(building || (busy && !schedule)) && (
        <div role="status" className="rounded-card border border-border bg-card p-6 text-muted">
          Building your schedule… this can take a few minutes. You can leave this page; it will be here when you come back.
        </div>
      )}

      {!schedule && building ? null : !schedule ? (
        <Card className="max-w-xl">
          <CardTitle>No schedule yet{termLabel ? ` for ${termLabel}` : ''}</CardTitle>
          <p className="mb-4 text-muted">The AI will pick your core courses first, then electives that fit your interests.</p>
          {notice && <NoticeBox n={notice} />}
          <Button disabled={busy} onClick={() => void build(false)}>
            {busy ? 'Building…' : 'Build my schedule'}
          </Button>
        </Card>
      ) : (
        <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[62fr_38fr]">
          <DraftCard
            s={schedule}
            term={term}
            termLabel={termLabel}
            qpaNote={d?.progress.qpa == null}
            busy={busy}
            notice={notice}
            act={act}
            setConfirm={setConfirm}
            onBuild={build}
            onSubmitted={setSubmitChecks}
            onView={(id) => setViewId(id === current?.schedule_id ? null : id)}
          />
          <RightColumn
            s={schedule}
            d={d}
            advisorName={d?.advisor?.name}
            busy={busy}
            act={act}
            term={term}
            onBack={viewingOld ? () => setViewId(null) : null}
          />
        </div>
      )}

      {d && <StudentMessages studentId={d.student.student_id} advisorName={d.advisor?.name} />}

      {submitChecks && <SubmitResult s={submitChecks} onClose={() => setSubmitChecks(null)} />}
      {confirm && <ConfirmDialog c={confirm} onClose={() => setConfirm(null)} act={act} />}
    </div>,
  )
}

function shell(name: string | undefined, term: string, body: ReactNode) {
  return <PageShell top={<StudentTopBar name={name} scheduleTo={`/schedule/${term}`} />}>{body}</PageShell>
}

function NoticeBox({ n }: { n: Notice }) {
  return (
    <div role="alert" className="mb-4 rounded-control border border-danger-text/30 bg-danger-bg px-4 py-3 text-[14px] text-danger-text">
      <p className="font-medium">{n.title}</p>
      {n.reasons.length > 0 && (
        <ul className="mt-1 list-disc pl-5">
          {n.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ---------- confirm dialog ----------
function ConfirmDialog({ c, onClose, act }: { c: NonNullable<Confirm>; onClose: () => void; act: (f: () => Promise<undefined>) => Promise<unknown> }) {
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-ink/40 p-4" role="dialog" aria-modal="true" aria-label={c.title}>
      <div className="w-full max-w-md rounded-card bg-card p-6">
        <h2 className="text-[20px] font-semibold">{c.title}</h2>
        <p className="mt-2 text-muted">{c.body}</p>
        <div className="mt-6 flex justify-end gap-3">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant={c.danger ? 'danger' : 'primary'}
            onClick={() => {
              onClose()
              void act(() => c.run().then(() => undefined))
            }}
          >
            {c.action}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ---------- left: my draft ----------
type Act = (call: () => Promise<ScheduleView | undefined>, fallback?: string) => Promise<ScheduleView | undefined>

function DraftCard(props: {
  s: ScheduleView
  term: string
  termLabel: string
  qpaNote: boolean
  busy: boolean
  notice: Notice | null
  act: Act
  setConfirm: (c: Confirm) => void
  onBuild: (replace: boolean) => Promise<ScheduleView | undefined>
  onSubmitted: (s: ScheduleView) => void
  onView: (id: string) => void
}) {
  const { s, busy, act } = props
  const isDraft = s.status === 'draft'
  const navigate = useNavigate()
  const [showHistory, setShowHistory] = useState(false)
  const [explain, setExplain] = useState(false)
  const total = num(s.total_units)
  const core = s.items.filter((i) => i.item_type === 'core')
  const electives = s.items.filter((i) => i.item_type === 'elective')
  const extended = s.max_units_extended ?? s.max_units
  const isExtendedLoad = total > s.max_units && total <= extended && s.extended_allowed
  const unitsBlock = s.blocking.find((b) => b.startsWith('UNIT_LIMIT'))
  const disabledReason = !s.can_submit && s.status === 'draft' ? (s.items.length === 0 ? 'Add courses first.' : strip(unitsBlock ?? s.blocking[0] ?? '')) : ''
  const post = (path: string) => () => api.post<ScheduleView>(`/schedules/${s.schedule_id}${path}`)
  const room = s.max_units - total

  const hint = room >= 6 ? `Room for one more ${unitsLabel(6)} elective` : room > 0 ? `${unitsLabel(room)} left` : total > s.max_units ? 'Above the standard limit' : 'Full load'

  return (
    <Card className={`p-8 ${s.status === 'draft' ? 'border-2 border-primary' : ''}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h2 className="text-[28px] font-semibold">{s.status === 'draft' ? 'My draft' : 'My schedule'}</h2>
          {s.edited_by_student && <Badge tone="warning">Edited by you</Badge>}
          <StatusBadge status={s.status} />
        </div>
        <div className="flex gap-2">
          {s.can_reset && (
            <Button variant="outline" disabled={busy} onClick={() => void act(post('/reset'), 'Could not reset')}>
              Reset to AI suggestion
            </Button>
          )}
          {s.can_discard && (
            <Button
              variant="danger"
              disabled={busy}
              onClick={() =>
                props.setConfirm({
                  title: 'Discard this draft?',
                  body: 'It stays in your history and your advisor can still see it.',
                  action: 'Discard draft',
                  danger: true,
                  run: async () => {
                    await api.delete(`/schedules/${s.schedule_id}`)
                    navigate('/')
                  },
                })
              }
            >
              Discard draft
            </Button>
          )}
        </div>
      </div>

      {props.notice && <div className="mt-4"><NoticeBox n={props.notice} /></div>}

      <div className="mt-5">
        <UnitMeter units={total} limit={s.max_units} hint={isDraft ? hint : undefined} />
        {(isDraft || isExtendedLoad) && (
          <p className="mt-2 flex flex-wrap items-center gap-2 text-[13px] text-muted">
            {isDraft &&
              (s.extended_allowed && s.max_units_extended
                ? `Up to ${s.max_units_extended} allowed (QPA above ${num(s.extended_units_min_qpa).toFixed(2)}).`
                : `Above ${s.max_units} units needs a QPA above ${num(s.extended_units_min_qpa ?? 3.5).toFixed(2)}.`)}
            {isExtendedLoad && <Badge tone="warning">Extended load</Badge>}
          </p>
        )}
      </div>

      <div className="mt-5 rounded-card bg-page p-5">
        <div className="flex flex-wrap gap-10">
          <Stat label="Units" value={String(total)} sub={`of ${s.max_units} max`} />
          <Stat label="Core" value={String(num(s.core_units))} />
          <Stat label="Courses" value={String(s.items.length)} />
        </div>
        {s.checks.length > 0 && (
          <ul className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-[14px]">
            {s.checks.map((c) => (
              <li key={c.code} title={c.detail} className={c.passed ? 'text-success-text' : c.advisor_flag ? 'text-warning-text' : 'text-danger-text'}>
                {c.passed ? '✓' : c.advisor_flag ? '!' : '✗'} {humanCode(c.code)}
              </li>
            ))}
          </ul>
        )}
      </div>

      <h3 className="mb-1 mt-8 text-[16px] font-semibold">
        Core courses <span className="font-normal text-muted">· set by your program</span>
      </h3>
      {core.length === 0 ? <EmptyState>No core courses in this term.</EmptyState> : core.map((i) => <DraftRow key={i.course_code} s={s} i={i} term={props.term} busy={busy} act={act} />)}

      <h3 className="mb-1 mt-8 text-[16px] font-semibold">
        Electives <span className="font-normal text-muted">· yours to change</span>
      </h3>
      {electives.length === 0 ? (
        <EmptyState>No electives yet. Add one from the suggestions or search on the right.</EmptyState>
      ) : (
        electives.map((i) => <DraftRow key={i.course_code} s={s} i={i} term={props.term} busy={busy} act={act} />)
      )}

      {explain && (
        <div className="mt-6 space-y-3 rounded-card border border-border p-5 text-[15px]">
          <p className="font-semibold">How this schedule was built</p>
          <p className="whitespace-pre-line">{s.ai_explanation || 'No explanation was saved for this schedule.'}</p>
          {s.load_rationale && (
            <>
              <p className="font-semibold">Units this term</p>
              <p>{s.load_rationale}</p>
            </>
          )}
        </div>
      )}

      <div className="mt-8 flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-4 text-[14px]">
          <button className="text-primary underline" onClick={() => setShowHistory((v) => !v)}>
            Previous versions
          </button>
          {s.can_edit && (
            <button
              className="text-primary underline"
              disabled={busy}
              onClick={() =>
                props.setConfirm({
                  title: 'Replace your current draft?',
                  body: 'The AI builds a new version. Your current draft is kept in your history as superseded.',
                  action: 'Build again',
                  run: async () => void (await props.onBuild(true)),
                })
              }
            >
              Build again
            </button>
          )}
        </div>
        <div className="flex flex-col items-end gap-2">
          {busy && <p role="status" className="text-[14px] text-muted">Working… this can take up to a minute</p>}
          <div className="flex gap-3">
            <Button variant="outline" onClick={() => setExplain((v) => !v)}>
              Explain this schedule
            </Button>
            {s.status === 'draft' && (
              <Button
                disabled={busy || !s.can_submit}
                onClick={async () => {
                  const view = await act(post('/submit'), 'Could not send to your advisor')
                  if (view) props.onSubmitted(view)
                }}
              >
                Send to advisor
              </Button>
            )}
            {s.can_withdraw && (
              <Button variant="outline" disabled={busy} onClick={() => void act(post('/withdraw'), 'Could not withdraw')}>
                Withdraw
              </Button>
            )}
          </div>
          {disabledReason && <p className="max-w-sm text-right text-[13px] text-danger-text">{disabledReason}</p>}
        </div>
      </div>

      {showHistory && <History term={props.term} currentId={s.schedule_id} onView={props.onView} />}
    </Card>
  )
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <p className="text-[13px] text-muted">{label}</p>
      <p className="text-[24px] font-semibold">
        {value} {sub && <span className="text-[14px] font-normal text-muted">{sub}</span>}
      </p>
    </div>
  )
}

function StatusBadge({ status }: { status: ScheduleView['status'] }) {
  if (status === 'draft') return null
  const tone = status === 'approved' ? 'success' : status === 'rejected' ? 'danger' : 'warning'
  const label = status === 'submitted' ? 'Awaiting review' : status.charAt(0).toUpperCase() + status.slice(1)
  return <Badge tone={tone}>{label}</Badge>
}

function History({ term, currentId, onView }: { term: string; currentId: string; onView: (id: string) => void }) {
  const h = useGet<HistoryItem[]>(`/schedules/history?term=${enc(term)}`)
  return (
    <div className="mt-6 rounded-card border border-border p-5">
      <p className="mb-2 font-semibold">Previous versions</p>
      {h.loading && <p className="text-muted">Loading…</p>}
      {h.error && <p className="text-danger-text">{h.error}</p>}
      {h.data && h.data.length === 0 && <p className="text-muted">No versions yet.</p>}
      <ul className="divide-y divide-border text-[14px]">
        {h.data?.map((v) => (
          <li key={v.schedule_id} className="flex items-center justify-between gap-3 py-2">
            <span>
              Version {v.version} · {v.courses} courses · {unitsLabel(num(v.total_units))} · {fmtDate(v.created_at.slice(0, 10))}
              {v.schedule_id === currentId && ' (shown)'}
            </span>
            <span className="flex items-center gap-3">
              <Badge>{v.status}</Badge>
              {v.schedule_id !== currentId && (
                <button type="button" className="text-primary underline" onClick={() => onView(v.schedule_id)}>
                  View
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---------- one row in the draft ----------
function DraftRow({ s, i, term, busy, act }: { s: ScheduleView; i: ScheduleItem; term: string; busy: boolean; act: Act }) {
  const [open, setOpen] = useState(false)
  const [swapping, setSwapping] = useState(false)
  const byStudent = i.added_by === 'student' && i.item_type === 'elective'
  const editable = s.can_edit && !i.is_locked
  const edit = (body: Record<string, unknown>, fallback: string) => act(() => api.post<ScheduleView>(`/schedules/${s.schedule_id}/items`, body), fallback)
  const meta = [i.days_times, modalityLabel(i.modality), i.seats_left != null ? seatsLabel(i.seats_left) : null, i.match_score != null ? `${i.match_score.toFixed(2)} match` : null]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className={`border-b border-border last:border-b-0 ${byStudent ? 'bg-changed-bg' : ''} ${open ? 'rounded-card border border-primary/40' : ''}`}>
      <div className="flex items-start gap-4 px-2 py-3.5">
        <span className="w-16 shrink-0 pt-px font-mono text-[14px] font-medium">{i.course_code}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {i.title}
            {i.is_conditional && <Badge tone="blue">Conditional</Badge>}
            {i.needs_approval && <Badge tone="warning">Needs approval</Badge>}
            {byStudent && <Badge tone="warning">Added by you</Badge>}
          </div>
          <div className="text-[13px] text-muted">
            {meta}
            {i.ai_original_course && ` · replaces ${i.ai_original_course}`}
          </div>
          {i.issues.length > 0 && <div className="text-[13px] text-warning-text">{i.issues.map(strip).join(' · ')}</div>}
        </div>
        <span className="shrink-0 pt-px text-[14px] text-muted">{unitsLabel(num(i.units))}</span>
        <div className="flex shrink-0 items-center justify-end gap-2">
          {i.is_locked || i.item_type === 'core' ? (
            <RequiredPill />
          ) : editable ? (
            <>
              {byStudent ? (
                <Button
                  variant="outline"
                  className="h-10 px-4"
                  disabled={busy}
                  onClick={() =>
                    void edit(
                      i.ai_original_course
                        ? { action: 'swap', course_code: i.ai_original_course, replace_course_code: i.course_code }
                        : { action: 'remove', course_code: i.course_code },
                      'Could not undo',
                    )
                  }
                >
                  Undo
                </Button>
              ) : (
                <Button variant="outline" className="h-10 px-4" disabled={busy} onClick={() => setSwapping((v) => !v)}>
                  Swap
                </Button>
              )}
              <Button variant="danger" className="h-10 px-4" disabled={busy} onClick={() => void edit({ action: 'remove', course_code: i.course_code }, 'Could not remove')}>
                Remove
              </Button>
            </>
          ) : null}
          {i.item_type === 'elective' && (
            <Button variant="outline" className="h-10 w-10 px-0" aria-label={open ? 'Hide details' : 'About this course'} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
              {open ? '▴' : '▾'}
            </Button>
          )}
        </div>
      </div>
      {swapping && editable && <SwapPicker s={s} leaving={i} busy={busy} onPick={(code) => { setSwapping(false); void edit({ action: 'swap', course_code: code, replace_course_code: i.course_code }, 'Could not swap') }} />}
      {open && <About code={i.course_code} term={term} reason={i.reason} />}
    </div>
  )
}

function SwapPicker({ s, leaving, busy, onPick }: { s: ScheduleView; leaving: ScheduleItem; busy: boolean; onPick: (code: string) => void }) {
  const sug = useGet<SuggestionGroups>(`/schedules/${s.schedule_id}/suggestions`)
  const choices = [...(sug.data?.interest_match ?? []), ...(sug.data?.popular ?? [])].filter((x) => !x.in_schedule && x.course_code !== leaving.course_code)
  return (
    <div className="mx-2 mb-3 rounded-control border border-border bg-page p-3 text-[14px]">
      <p className="mb-2 font-medium">Swap {leaving.course_code} for…</p>
      {sug.loading && <p className="text-muted">Loading…</p>}
      {sug.data && choices.length === 0 && <p className="text-muted">No suggestions left. Use the search on the right and Add, then Remove this one.</p>}
      <ul className="space-y-1">
        {choices.map((c) => (
          <li key={c.course_code} className="flex items-center justify-between gap-3">
            <span>
              <span className="font-mono">{c.course_code}</span> {c.title}
            </span>
            <Button variant="outline" className="h-9 px-3" disabled={busy} onClick={() => onPick(c.course_code)}>
              Swap in
            </Button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function About({ code, term, reason }: { code: string; term: string; reason: string | null }) {
  const c = useGet<CourseFull>(`/courses/${enc(code)}?term=${enc(term)}`)
  const profs = [...new Set(c.data?.sections.flatMap((x) => x.instructors) ?? [])]
  return (
    <div className="grid grid-cols-1 gap-4 border-t border-border px-4 py-4 text-[14px] md:grid-cols-[1.4fr_1fr]">
      <div>
        <p className="font-semibold">About this course</p>
        {c.loading && <p className="text-muted">Loading…</p>}
        {c.error && <p className="text-danger-text">{c.error}</p>}
        {c.data && <p className="mt-1">{c.data.description || 'No description available.'}</p>}
        {reason && (
          <p className="mt-2">
            <strong>Why it&apos;s suggested:</strong> {reason}
          </p>
        )}
      </div>
      <div>
        <p className="font-semibold">{profs.length > 1 ? 'Professors' : 'Professor'}</p>
        <p>{profs.length ? profs.join(', ') : 'Not announced yet'}</p>
        {c.data?.syllabus_url && (
          <a className="mt-2 inline-block text-primary underline" href={c.data.syllabus_url} target="_blank" rel="noreferrer">
            Syllabus
          </a>
        )}
      </div>
    </div>
  )
}

// ---------- right column ----------
/** Notice by status; the suggestions and the search only while the schedule is a draft (nothing to edit otherwise). */
function RightColumn({ s, d, advisorName, busy, act, term, onBack }: { s: ScheduleView; d: Dashboard | null; advisorName?: string; busy: boolean; act: Act; term: string; onBack: (() => void) | null }) {
  const conditional = s.items.filter((i) => i.is_conditional)
  const showConditional = conditional.length > 0 && (s.status === 'draft' || s.status === 'submitted')

  return (
    <div className="space-y-6">
      <ScheduleNotice
        status={s.status}
        advisorName={advisorName}
        submittedAt={s.submitted_at}
        decidedAt={s.decided_at}
        advisorComment={s.advisor_comment}
        canResubmit={s.can_resubmit && !onBack}
        busy={busy}
        onResubmit={() => void act(() => api.post<ScheduleView>(`/schedules/${s.schedule_id}/resubmit`), 'Could not start a new version')}
        onBack={onBack}
      />

      {s.status === 'draft' && <EditingPanels s={s} d={d} busy={busy} act={act} term={term} />}

      {showConditional && (
        <Card>
          <Badge tone="blue">Conditional course</Badge>
          {conditional.map((i) => (
            <p key={i.course_code} className="mt-3">
              <strong className="font-mono">{i.course_code}</strong> {i.title}: {i.issues.map(strip).find((x) => x.startsWith('Conditional')) ?? 'needs a prerequisite that is in progress.'} If you don&apos;t pass it, the AI rebuilds your schedule and tells your advisor.
            </p>
          ))}
        </Card>
      )}
    </div>
  )
}

function EditingPanels({ s, d, busy, act, term }: { s: ScheduleView; d: Dashboard | null; busy: boolean; act: Act; term: string }) {
  const sug = useGet<SuggestionGroups>(`/schedules/${s.schedule_id}/suggestions`)
  const { profile } = useAuth()
  const programCode = profile?.programs[0]?.program_code // from the data, never typed into the screen
  const inDraft = new Set(s.items.map((i) => i.course_code))
  const hasInterests = (d?.interests.confirmed.length ?? 0) > 0
  const add = (code: string) => act(() => api.post<ScheduleView>(`/schedules/${s.schedule_id}/items`, { action: 'add', course_code: code }), 'Could not add')

  return (
    <>
      <Card>
        <CardTitle aside={`${s.term_label} · ranked for you`}>Suggested electives</CardTitle>
        {sug.loading && <p className="text-muted">Loading…</p>}
        {sug.error && <p className="text-danger-text">{sug.error}</p>}
        {sug.data && !hasInterests && (
          <div className="mb-4">
            <InfoBanner action={<Link to="/interests"><Button className="h-10 shrink-0">Tell us your interests</Button></Link>}>
              <strong>You haven&apos;t saved any interests yet.</strong> For now these are the electives most chosen by students in your program.
            </InfoBanner>
          </div>
        )}
        {sug.data && sug.data.interest_match.length + sug.data.popular.length === 0 && <EmptyState>No suggestions for this term.</EmptyState>}
        {sug.data && sug.data.interest_match.length > 0 && <Group title="Matches your interests" items={sug.data.interest_match} inDraft={inDraft} s={s} busy={busy} add={add} />}
        {sug.data && sug.data.popular.length > 0 && <Group title={programCode ? `Popular with ${programCode} students` : 'Popular in your program'} items={sug.data.popular} inDraft={inDraft} s={s} busy={busy} add={add} />}
        {sug.data && <p className="mt-3 text-[13px] text-muted">Only electives offered this term with open seats are shown.</p>}
      </Card>

      <SearchCard s={s} term={term} inDraft={inDraft} busy={busy} add={add} />
    </>
  )
}

function Group({ title, items, inDraft, s, busy, add }: { title: string; items: Suggestion[]; inDraft: Set<string>; s: ScheduleView; busy: boolean; add: (code: string) => void }) {
  return (
    <div className="mb-4">
      <h3 className="mb-2 text-[15px] font-semibold">{title}</h3>
      <ul className="space-y-3">
        {items.map((x) => {
          const mine = inDraft.has(x.course_code)
          const clash = x.flags.find((f) => f.startsWith('time_clash'))
          return (
            <li key={x.course_code} className="rounded-card border border-border p-4">
              <div className="flex items-start justify-between gap-3">
                <p>
                  <span className="font-mono text-[14px] font-medium">{x.course_code}</span> <span className="font-medium">{x.title}</span>
                </p>
                {mine ? <Badge tone="primary">In your draft</Badge> : s.can_edit && <Button className="h-9 px-4" disabled={busy} onClick={() => add(x.course_code)}>Add</Button>}
              </div>
              <div className="mt-2"><MatchBar score={x.final_score} /></div>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px] text-muted">
                {x.units != null && <span>{unitsLabel(num(x.units))}</span>}
                {x.matched_interest && <Badge tone="primary">{x.matched_interest}</Badge>}
                {clash && <Badge tone="warning">Time clash{clash.includes(':') ? ` with ${clash.split(':')[1]}` : ''}</Badge>}
                {x.flags.includes('few_seats') && <Badge tone="warning">Few seats left</Badge>}
                {x.group_code === 'popular' && !x.matched_interest && <Badge>Popular</Badge>}
              </div>
              {x.reason && <p className="mt-2 text-[13px] text-muted">{x.reason}</p>}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function SearchCard({ s, term, inDraft, busy, add }: { s: ScheduleView; term: string; inDraft: Set<string>; busy: boolean; add: (code: string) => void }) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<CourseHit[] | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    const text = q.trim()
    if (text.length < 2) {
      setHits(null)
      setErr(null)
      return
    }
    let cancelled = false
    const t = setTimeout(() => {
      api
        .get<{ results: CourseHit[] }>(`/courses/search?q=${enc(text)}&term=${enc(term)}`)
        .then((r) => !cancelled && (setHits(r.results), setErr(null)))
        .catch((e: unknown) => !cancelled && setErr(e instanceof ApiError ? e.message : 'Search failed'))
    }, 300)
    return () => {
      cancelled = true
      clearTimeout(t)
    }
  }, [q, term])

  return (
    <Card>
      <CardTitle>Search all electives</CardTitle>
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search by title or code, e.g. deep learning"
        aria-label="Search all electives"
        className="h-11 w-full rounded-control border border-border px-3 text-[15px]"
      />
      {err && <p className="mt-3 text-danger-text">{err}</p>}
      {hits && hits.length === 0 && <p className="mt-3 text-muted">No courses found.</p>}
      <ul className="mt-2 divide-y divide-border">
        {hits?.map((h) => {
          const v = h.check
          const blocked = v ? !v.eligible : false
          const reason = v ? (blocked ? v.blocking.map(strip).join(' ') : v.notes.map(strip).join(' ')) : ''
          const badge = blocked ? (v!.blocking.some((b) => b.startsWith('PREREQ')) ? 'Prerequisite' : v!.blocking.some((b) => b.startsWith('LEVEL')) ? 'Not allowed' : 'Blocked') : v?.flags.includes('needs_petition') ? 'Needs petition' : v?.flags.includes('needs_approval') ? 'Needs approval' : null
          return (
            <li key={h.course_code} className="flex items-start justify-between gap-3 py-3">
              <div className="min-w-0">
                <p>
                  <span className="font-mono text-[14px] font-medium">{h.course_code}</span> {h.title}
                </p>
                <p className={`text-[13px] ${blocked ? 'text-danger-text' : 'text-muted'}`}>
                  {reason || [h.units != null ? unitsLabel(num(h.units)) : null, h.seats_left != null ? seatsLabel(h.seats_left) : null].filter(Boolean).join(' · ')}
                </p>
              </div>
              {inDraft.has(h.course_code) ? (
                <Badge tone="primary">In your draft</Badge>
              ) : blocked ? (
                <Badge tone="danger">{badge}</Badge>
              ) : (
                <div className="flex shrink-0 flex-col items-end gap-1">
                  {badge && <Badge tone="warning">{badge}</Badge>}
                  {s.can_edit && <Button className="h-9 px-4" disabled={busy} onClick={() => add(h.course_code)}>Add</Button>}
                </div>
              )}
            </li>
          )
        })}
      </ul>
      <p className="mt-3 text-[13px] text-muted">
        Only courses offered this term with open seats are listed. Prerequisites, course level, approval, time clashes and the unit limit are checked by the rules on every change.
      </p>
    </Card>
  )
}

// ---------- after "Send to advisor" ----------
function SubmitResult({ s, onClose }: { s: ScheduleView; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-ink/40 p-4" role="dialog" aria-modal="true" aria-label="Sent to your advisor">
      <div className="w-full max-w-lg rounded-card bg-card p-6">
        <h2 className="text-[20px] font-semibold">Sent to your advisor</h2>
        <p className="mt-1 text-muted">Your advisor will review it. You can withdraw it until they decide. Result of the checks:</p>
        <ul className="mt-4 space-y-2 text-[14px]">
          {s.checks.map((c) => (
            <li key={c.code} className="flex gap-2">
              <span className={c.passed ? 'text-success-text' : 'text-warning-text'}>{c.passed ? '✓' : '!'}</span>
              <span>
                <strong>{humanCode(c.code)}</strong> <span className="text-muted">{c.detail}</span>
              </span>
            </li>
          ))}
        </ul>
        <div className="mt-6 flex justify-end">
          <Button onClick={onClose}>Close</Button>
        </div>
      </div>
    </div>
  )
}
