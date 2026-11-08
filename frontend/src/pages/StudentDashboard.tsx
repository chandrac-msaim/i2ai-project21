import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { num, type CoreStatus, type CourseDetail, type Dashboard, type TermStatus } from '../api/types'
import { useGet } from '../api/useApi'
import { PageShell, StudentTopBar } from '../components/TopBar'
import { Badge, Button, Card, CardTitle, CourseRow, EmptyState, ErrorState, InfoBanner, PageSkeleton, StatusPill } from '../components/ui'
import { fmtDate, pct, unitsLabel } from '../lib/format'

export default function StudentDashboard() {
  const { data, error, loading, reload } = useGet<Dashboard>('/students/me/dashboard')
  const openTerm = data?.terms.plannable[0]
  const name = data ? `${data.student.first_name} ${data.student.last_name}` : undefined

  return (
    <PageShell top={<StudentTopBar name={name} scheduleTo={openTerm ? `/schedule/${openTerm.term_code}` : undefined} />}>
      {loading && !data && <PageSkeleton />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && <DashboardBody d={data} />}
    </PageShell>
  )
}

function DashboardBody({ d }: { d: Dashboard }) {
  const navigate = useNavigate()
  const open = d.terms.plannable[0]
  const suggested = d.interests.suggested.length

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-[36px] font-semibold leading-tight">
          {d.is_new_student ? 'Welcome' : 'Welcome back'}, {d.student.first_name}
        </h1>
        <p className="mt-1 text-[17px] text-muted">
          {d.student.university_name} · {d.student.program.title} · Term {d.audit.plan_position} of {d.student.academic_terms} ·{' '}
          {open ? `${open.term_label} planning is open` : 'No term is open for planning right now'}
        </p>
      </div>

      {suggested > 0 && (
        <InfoBanner
          action={
            <Button className="shrink-0" onClick={() => navigate('/interests')}>
              Review interests
            </Button>
          }
        >
          <strong>
            We found {suggested} possible {suggested === 1 ? 'interest' : 'interests'} in your resume and undergraduate degree.
          </strong>{' '}
          Confirm them so your elective suggestions fit you. Nothing is saved until you say yes.
        </InfoBanner>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[62fr_38fr]">
        <ProgressCard d={d} />
        <AdvisorCard d={d} />
      </div>

      <Card className="grid grid-cols-1 gap-x-12 gap-y-10 lg:grid-cols-[62fr_38fr]">
        <CoreCourses d={d} />
        <div>
          <ElectivesCard d={d} openTerm={open} />
          <InterestsCard d={d} />
        </div>
      </Card>
    </div>
  )
}

// ---------- degree progress ----------
function ProgressCard({ d }: { d: Dashboard }) {
  const { audit, progress } = d
  const total = progress.total_units
  const done = num(progress.units_completed)
  const inProgress = num(progress.units_in_progress)
  const waived = audit.core.filter((c) => c.status === 'waived').reduce((s, c) => s + num(c.units), 0)
  const remaining = Math.max(total - done - inProgress - waived, 0)
  const donePct = pct(done, total)
  const afterPct = pct(done + inProgress, total)
  const seg = (n: number) => `${(n / total) * 100}%`
  const qpa = progress.qpa == null ? '-' : num(progress.qpa).toFixed(2)

  const legend: { color: string; label: string; show: boolean }[] = [
    { color: 'bg-primary', label: `Completed ${unitsLabel(done)}`, show: true },
    { color: 'bg-accent-blue-text', label: `Waived ${unitsLabel(waived)}`, show: waived > 0 },
    { color: 'bg-warning-text', label: `In progress ${unitsLabel(inProgress)}`, show: true },
    { color: 'bg-border', label: `Remaining ${unitsLabel(remaining)}`, show: true },
  ]

  return (
    <Card className="p-8">
      <p className="text-muted">Degree progress</p>
      <div className="mt-1 flex items-start justify-between gap-6">
        <div className="flex items-baseline gap-3">
          <span className="text-[64px] font-semibold leading-none">{donePct}%</span>
          <span className="text-muted">
            of units done{inProgress > 0 && afterPct !== donePct ? ` · ${afterPct}% after this term` : ''}
          </span>
        </div>
        <div className="text-right">
          <p className="text-[14px] text-muted">Projected graduation</p>
          <p className="text-[24px] font-semibold">{progress.projected_graduation_label ?? '-'}</p>
        </div>
      </div>

      <div className="mt-6 flex h-3 overflow-hidden rounded-full bg-border" role="img" aria-label={`${donePct}% of units done`}>
        <div className="bg-primary" style={{ width: seg(done) }} />
        <div className="bg-accent-blue-text" style={{ width: seg(waived) }} />
        <div className="bg-warning-text" style={{ width: seg(inProgress) }} />
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-[14px]">
        {legend
          .filter((l) => l.show)
          .map((l) => (
            <li key={l.label} className="flex items-center gap-2">
              <span className={`h-3 w-3 rounded-[3px] ${l.color}`} />
              {l.label}
            </li>
          ))}
      </ul>

      <p className="mt-6 border-t border-border pt-4 text-[14px] text-muted">
        QPA {qpa} (cumulative) · program minimum {num(progress.min_qpa).toFixed(1)}
        {progress.below_min_qpa && <Badge tone="danger" className="ml-2">Below minimum</Badge>}
      </p>
    </Card>
  )
}

// ---------- advisor + term picker ----------
function AdvisorCard({ d }: { d: Dashboard }) {
  const { advisor, unread_messages: unread } = d
  // the thread is shown on the schedule page; open it for the term being planned (else the latest closed one)
  const messageTerm = d.terms.plannable[0]?.term_code ?? d.terms.recent_closed[0]?.term_code
  return (
    <Card>
      <p className="text-muted">Your advisor</p>
      <p className="mt-1 text-[20px] font-semibold">{advisor ? advisor.name : 'Not assigned yet'}</p>

      {unread > 0 && (
        <div className="mt-4 flex items-center gap-3 rounded-control border border-border px-4 py-3">
          <svg className="h-5 w-5 text-primary" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" strokeLinejoin="round" />
          </svg>
          <span className="flex-1">
            {unread} new {unread === 1 ? 'message' : 'messages'}
            {advisor ? ` from ${advisor.name}` : ''}
            {messageTerm && (
              <>
                {' · '}
                <Link to={`/schedule/${messageTerm}#messages`} className="text-primary underline">Read {unread === 1 ? 'it' : 'them'}</Link>
              </>
            )}
          </span>
          <Badge tone="primary">New</Badge>
        </div>
      )}

      <TermPicker d={d} />
    </Card>
  )
}

function TermPicker({ d }: { d: Dashboard }) {
  const navigate = useNavigate()
  const { plannable, upcoming, recent_closed } = d.terms
  const rows: { term: TermStatus; kind: 'open' | 'future' | 'nodata' | 'closed' }[] = [
    ...plannable.map((term) => ({ term, kind: 'open' as const })),
    ...upcoming.map((term) => ({ term, kind: term.status === 'open' ? ('nodata' as const) : ('future' as const) })),
    ...recent_closed.map((term) => ({ term, kind: 'closed' as const })),
  ]

  return (
    <div className="mt-5">
      <h3 className="mb-2 text-[16px] font-semibold">Choose a term to plan</h3>
      {rows.length === 0 ? (
        <EmptyState>No terms to show yet.</EmptyState>
      ) : (
        <ul className="space-y-2">
          {rows.map(({ term, kind }) => {
            const existing = d.schedules.find((s) => s.term_code === term.term_code)
            return (
              <li key={term.term_code} className="rounded-control border border-border px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="font-medium">{term.term_label}</p>
                    <p className="text-[13px] text-muted">
                      {kind === 'future' || kind === 'nodata'
                        ? `Registration opens ${fmtDate(term.registration_opens)}`
                        : `Registration ${kind === 'closed' ? 'closed' : 'closes'} ${fmtDate(term.registration_closes)}`}
                    </p>
                  </div>
                  {kind === 'open' && (
                    <Button className="h-10 shrink-0" onClick={() => navigate(`/schedule/${term.term_code}`)}>
                      {existing ? 'Open my schedule' : 'Build my schedule'}
                    </Button>
                  )}
                  {kind === 'closed' && <Badge>Closed</Badge>}
                  {kind === 'future' && <Badge tone="info">Not open yet</Badge>}
                  {kind === 'nodata' && <Badge tone="warning">No course data yet</Badge>}
                </div>
                {existing && (
                  <p className="mt-1 text-[13px] text-muted">
                    Your schedule: version {existing.version}, {existing.status}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

// ---------- core courses ----------
/** Blue note for a remaining course whose prerequisite is only in progress. Display only: the rule engine decides
 *  eligibility when the schedule is built. */
function useConditionalNotes(core: CoreStatus[], hasInProgress: boolean): Record<string, string> {
  const [notes, setNotes] = useState<Record<string, string>>({})

  useEffect(() => {
    if (!hasInProgress) return
    let cancelled = false
    const done = new Set(core.filter((c) => c.status === 'completed' || c.status === 'waived').map((c) => c.course_code))
    const running = new Set(core.filter((c) => c.status === 'in_progress').map((c) => c.course_code))
    const remaining = core.filter((c) => c.status === 'remaining')

    Promise.all(
      remaining.map((c) =>
        api
          .get<CourseDetail>(`/courses/${encodeURIComponent(c.course_code)}`)
          .then((detail) => [c.course_code, detail] as const)
          .catch(() => null),
      ),
    ).then((results) => {
      if (cancelled) return
      const next: Record<string, string> = {}
      for (const r of results) {
        if (!r) continue
        const [code, detail] = r
        const { options, catalog_applies } = detail.prerequisites
        if (!catalog_applies) continue
        // some option is fully covered by completed/waived/in-progress courses, but none by completed/waived alone
        const coveredByNow = options.some((opt) => opt.length > 0 && opt.every((p) => done.has(p) || running.has(p)))
        const coveredByDone = options.some((opt) => opt.every((p) => done.has(p)))
        if (coveredByNow && !coveredByDone) {
          const waiting = [...new Set(options.flat().filter((p) => running.has(p)))]
          next[code] = `Needs ${waiting.join(', ')} (in progress): can be taken next term`
        }
      }
      setNotes(next)
    })
    return () => {
      cancelled = true
    }
  }, [core, hasInProgress])

  return notes
}

function CoreCourses({ d }: { d: Dashboard }) {
  const core = d.audit.core
  const counts = {
    done: core.filter((c) => c.status === 'completed' || c.status === 'waived').length,
    progress: core.filter((c) => c.status === 'in_progress').length,
  }
  const notes = useConditionalNotes(core, counts.progress > 0)

  return (
    <div>
      <CardTitle aside={`Complete all ${core.length} · ${counts.done} done · ${counts.progress} in progress`}>Core courses</CardTitle>
      {core.length === 0 ? (
        <EmptyState>No core courses found for your track.</EmptyState>
      ) : (
        core.map((c) => (
          <CourseRow
            key={c.course_code}
            code={c.course_code}
            title={c.title}
            note={c.status === 'remaining' ? notes[c.course_code] : undefined}
            units={unitsLabel(num(c.units))}
            right={<StatusPill status={c.status} />}
          />
        ))
      )}
    </div>
  )
}

// ---------- electives + interests ----------
function ElectivesCard({ d, openTerm }: { d: Dashboard; openTerm?: TermStatus }) {
  const a = d.audit
  const required = num(a.elective_units_required)
  const done = num(a.elective_units_done)
  const left = num(a.elective_units_left)
  return (
    <div>
      <CardTitle aside={`Choose ${unitsLabel(required)} · ${unitsLabel(done)} done or in progress`}>Electives</CardTitle>
      <div className="rounded-card border border-dashed border-border px-4 py-3.5">
        {left > 0 ? `${unitsLabel(left)} of electives still open. ` : 'Your elective units are covered. '}
        <Link to={openTerm ? `/schedule/${openTerm.term_code}` : '/interests'} className="text-primary underline">
          See suggestions
        </Link>
      </div>
    </div>
  )
}

function InterestsCard({ d }: { d: Dashboard }) {
  const confirmed = d.interests.confirmed
  return (
    <div className="mt-8">
      <CardTitle aside={<Link to="/interests" className="text-primary underline">Edit</Link>}>Your interests</CardTitle>
      {confirmed.length === 0 ? (
        <EmptyState>
          You haven&apos;t saved any interests yet. <Link to="/interests" className="text-primary underline">Add some</Link> or use popular courses.
        </EmptyState>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            {confirmed.map((i) => (
              <span key={i.interest_id} className="rounded-full bg-page px-3.5 py-1.5 text-[14px]">
                {i.label}
              </span>
            ))}
          </div>
          <p className="mt-3 text-[14px] text-muted">Your advisor can see these.</p>
        </>
      )}
    </div>
  )
}
