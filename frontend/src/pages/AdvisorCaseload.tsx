import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { num, type Caseload, type CaseloadRow } from '../api/types'
import { useGet } from '../api/useApi'
import { PageShell, AdvisorTopBar } from '../components/TopBar'
import { Badge, Button, Card, EmptyState, ErrorState, PageSkeleton, type BadgeTone } from '../components/ui'

export const FLAG_LABEL: Record<string, { label: string; tone: BadgeTone }> = {
  LOW_QPA: { label: 'Low QPA', tone: 'danger' },
  BEHIND_PACE: { label: 'Behind pace', tone: 'warning' },
  CONDITIONAL_PREREQ: { label: 'Conditional prerequisite', tone: 'blue' },
  LOW_SEATS: { label: 'Low seats', tone: 'warning' },
  NEEDS_APPROVAL: { label: 'Needs approval', tone: 'info' },
  UNDERGRAD_CAP: { label: 'Undergraduate cap', tone: 'warning' },
  UNIT_OVERLOAD: { label: 'Extended load', tone: 'warning' },
}

const STATUS: Record<string, { label: string; tone: BadgeTone }> = {
  submitted: { label: 'Awaiting review', tone: 'warning' },
  approved: { label: 'Approved', tone: 'success' },
  rejected: { label: 'Rejected', tone: 'danger' },
  draft: { label: 'Draft', tone: 'neutral' },
}

type Filter = 'all' | 'flagged' | 'review' | 'unread'
const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'flagged', label: 'Flagged' },
  { key: 'review', label: 'Awaiting review' },
  { key: 'unread', label: 'Unread messages' },
]

const matches = (r: CaseloadRow, f: Filter) =>
  f === 'all' ||
  (f === 'flagged' && r.open_flags > 0) ||
  (f === 'review' && r.schedule?.status === 'submitted') ||
  (f === 'unread' && r.unread_messages > 0)

export default function AdvisorCaseload() {
  const { data, error, loading, reload } = useGet<Caseload>('/advisor/caseload')
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState('')
  const [program, setProgram] = useState('') // '' = all programs; the filter shows only for an advisor with 2+ programs
  const raw = params.get('filter')
  const filter: Filter = FILTERS.some((f) => f.key === raw) ? (raw as Filter) : 'all'

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (data?.rows ?? []).filter(
      (r) => matches(r, filter) && (!program || r.program_title === program) && (!q || `${r.first_name} ${r.last_name}`.toLowerCase().includes(q) || r.student_id.toLowerCase().includes(q)),
    )
  }, [data, filter, query, program])

  const m = data?.metrics
  return (
    <PageShell top={<AdvisorTopBar name={data?.advisor_name ?? undefined} title={data?.advisor_title ?? undefined} reviews={m?.awaiting_review} messages={m?.unread_messages} />}>
      <h1 className="text-[40px] font-semibold leading-tight">My caseload</h1>
      {data && (
        <p className="mt-1 text-[17px] text-muted">
          {[data.university?.name, ...data.programs.map((p) => p.title)].filter(Boolean).join(' · ')}
        </p>
      )}

      {loading && !data && <div className="mt-8"><PageSkeleton /></div>}
      {error && <div className="mt-8"><ErrorState message={error} onRetry={reload} /></div>}

      {data && m && (
        <>
          <div className="mt-8 grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Metric label="Students" value={m.students} />
            <Metric label="With risk flags" value={m.with_risk_flags} red={m.with_risk_flags > 0} />
            <Metric label="Schedules awaiting review" value={m.awaiting_review} />
            <Metric label="Unread messages" value={m.unread_messages} />
          </div>

          <Card className="mt-6 p-0">
            <div className="flex flex-wrap items-center justify-between gap-4 p-6">
              <div className="flex flex-wrap gap-2" role="tablist" aria-label="Filter students">
                {FILTERS.map((f) => (
                  <button
                    key={f.key}
                    role="tab"
                    aria-selected={filter === f.key}
                    onClick={() => setParams(f.key === 'all' ? {} : { filter: f.key })}
                    className={`h-11 rounded-full border px-5 text-[15px] font-medium ${
                      filter === f.key ? 'border-primary bg-primary text-white' : 'border-border bg-card hover:bg-page'
                    }`}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
              {data.programs.length > 1 && (
                <label className="flex items-center gap-3 text-muted">
                  Program
                  <select value={program} onChange={(e) => setProgram(e.target.value)} className="h-11 rounded-control border border-border bg-card px-3 text-ink">
                    <option value="">All programs</option>
                    {data.programs.map((pr) => <option key={pr.program_code} value={pr.title}>{pr.title}</option>)}
                  </select>
                </label>
              )}
              <label className="flex items-center gap-3 text-muted">
                Search
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Name or student ID"
                  className="h-11 w-64 rounded-control border border-border px-3 text-ink"
                />
              </label>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-y border-border text-[12px] uppercase tracking-wider text-muted">
                    {['Student', 'Term', 'Units done', 'QPA', 'Risk flags', 'Schedule', 'Messages', ''].map((h) => (
                      <th key={h} className="px-6 py-3 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <Row key={r.student_id} r={r} showProgram={data.programs.length > 1} />
                  ))}
                </tbody>
              </table>
            </div>
            {rows.length === 0 && <div className="p-6"><EmptyState>No students match this filter.</EmptyState></div>}
            <p className="border-t border-border px-6 py-4 text-[14px] text-muted">
              Showing {rows.length} of {data.rows.length} students
            </p>
          </Card>

          <div className="mt-6 flex flex-wrap justify-between gap-4 text-[14px] text-muted">
            <p className="max-w-[760px]">
              Flags come from fixed rules: QPA below the program minimum, behind the planned pace, more than the standard unit limit planned, a
              prerequisite still in progress, few seats left, an undergraduate course, or a course that needs special approval.
            </p>
            <p>You see only students in your caseload. Every access is logged.</p>
          </div>
        </>
      )}
    </PageShell>
  )
}

function Metric({ label, value, red }: { label: string; value: number; red?: boolean }) {
  return (
    <Card className="p-6">
      <p className="text-muted">{label}</p>
      <p className={`mt-2 text-[44px] font-semibold leading-none ${red ? 'text-danger-text' : ''}`}>{value}</p>
    </Card>
  )
}

/** showProgram: the university / program line is repeated per student only when the advisor has more than one program
 *  (otherwise it is already under the page title and in the tooltip). */
function Row({ r, showProgram }: { r: CaseloadRow; showProgram: boolean }) {
  const s = r.schedule
  const status = s ? STATUS[s.status] ?? { label: s.status, tone: 'neutral' as const } : { label: 'Not started', tone: 'neutral' as const }
  const highlight = s?.status === 'submitted' && r.open_flags > 0
  return (
    <tr className={`border-b border-border last:border-b-0 ${highlight ? 'bg-changed-bg' : ''}`}>
      <td className="px-6 py-4" title={`${r.university_name} · ${r.program_title}`}>
        <p className="font-semibold">{r.first_name} {r.last_name}</p>
        <p className="font-mono text-[13px] text-muted">{r.student_id}</p>
        {showProgram && <p className="text-[12px] text-muted">{r.university_name} · {r.program_title}</p>}
      </td>
      <td className="px-6 py-4">
        <p>{s ? s.term_label : '-'}</p>
        <p className="text-[13px] text-muted">{r.track_code}</p>
      </td>
      <td className="px-6 py-4">{num(r.units_completed)} / {r.total_units}</td>
      <td className={`px-6 py-4 font-semibold ${r.below_min_qpa ? 'text-danger-text' : ''}`}>{r.qpa == null ? '-' : num(r.qpa).toFixed(2)}</td>
      <td className="px-6 py-4">
        {r.flag_codes.length === 0 ? (
          <Badge tone="success">None</Badge>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {r.flag_codes.map((c) => {
              const f = FLAG_LABEL[c] ?? { label: c, tone: 'neutral' as const }
              return <Badge key={c} tone={f.tone}>{f.label}</Badge>
            })}
          </div>
        )}
      </td>
      <td className="px-6 py-4"><Badge tone={status.tone}>{status.label}</Badge></td>
      <td className="px-6 py-4">{r.unread_messages > 0 ? <Badge tone="primary">{r.unread_messages} new</Badge> : <span className="text-muted">None</span>}</td>
      <td className="px-6 py-4 text-right">
        {s ? (
          <Link to={`/advisor/schedules/${s.schedule_id}`}>
            <Button variant={s.status === 'submitted' ? 'primary' : 'outline'} className="h-11 px-6">
              {s.status === 'submitted' ? 'Review' : 'Open'}
            </Button>
          </Link>
        ) : (
          <span className="text-[13px] text-muted">No schedule yet</span>
        )}
      </td>
    </tr>
  )
}
