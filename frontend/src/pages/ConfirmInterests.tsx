import { useRef, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { Dashboard, InterestLists, InterestView, TermPicker, UploadResult } from '../api/types'
import { useGet } from '../api/useApi'
import { PageShell, StudentTopBar } from '../components/TopBar'
import { Badge, Button, Card, EmptyState, ErrorState, PageSkeleton } from '../components/ui'

type DocType = 'resume' | 'undergrad_transcript'

const SOURCE_LINE: Record<InterestView['source'], string> = {
  resume: 'From your resume',
  undergrad: 'From your undergraduate degree',
  ai_suggested: 'Suggested by the AI',
  manual: 'Added by you',
}

/** The lists after one interest changed status: kept -> confirmed, removed -> gone. */
export function applyInterest(lists: InterestLists, updated: InterestView): InterestLists {
  const others = (xs: InterestView[]) => xs.filter((x) => x.interest_id !== updated.interest_id)
  return {
    suggested: updated.status === 'suggested' ? [...others(lists.suggested), updated] : others(lists.suggested),
    confirmed: updated.status === 'confirmed' ? [...others(lists.confirmed), updated] : others(lists.confirmed),
  }
}

export default function ConfirmInterests() {
  const navigate = useNavigate()
  const lists = useGet<InterestLists>('/students/me/interests')
  const terms = useGet<TermPicker>('/terms/plannable')
  const me = useGet<Dashboard>('/students/me/dashboard')

  const [busyId, setBusyId] = useState<number | null>(null)
  const [busyAction, setBusyAction] = useState<'confirm' | 'remove' | null>(null)
  const inFlight = useRef(new Set<number>()) // blocks a second click before React has re-rendered the disabled button
  const [actionError, setActionError] = useState<string | null>(null)
  const openTerm = terms.data?.plannable[0]
  const name = me.data ? `${me.data.student.first_name} ${me.data.student.last_name}` : undefined

  // suggested + kept (resume / undergrad) share one list, in the order they were found
  const background = lists.data
    ? [...lists.data.suggested, ...lists.data.confirmed.filter((i) => i.source !== 'manual')].sort((a, b) => a.interest_id - b.interest_id)
    : []
  const own = lists.data?.confirmed.filter((i) => i.source === 'manual') ?? []
  const keptCount = lists.data?.confirmed.length ?? 0

  async function act(i: InterestView, action: 'confirm' | 'remove') {
    if (inFlight.current.has(i.interest_id)) return
    inFlight.current.add(i.interest_id)
    setBusyId(i.interest_id)
    setBusyAction(action)
    setActionError(null)
    try {
      const updated = await api.patch<InterestView>(`/students/me/interests/${i.interest_id}`, { action })
      // the response is the new state of that one interest: apply it at once (no refetch, no flicker, no second round trip)
      if (lists.data) lists.setData(applyInterest(lists.data, updated))
      else lists.reload()
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : 'Could not save that change')
    } finally {
      inFlight.current.delete(i.interest_id)
      setBusyId(null)
      setBusyAction(null)
    }
  }

  const goOn = () => navigate(openTerm ? `/schedule/${openTerm.term_code}` : '/')

  return (
    <PageShell top={<StudentTopBar name={name} scheduleTo={openTerm ? `/schedule/${openTerm.term_code}` : undefined} />}>
      <p className="text-[15px] text-muted">Step 1 of 2 · before we suggest electives</p>
      <h1 className="mt-2 text-[40px] font-semibold leading-tight">Are these your interests?</h1>
      <p className="mt-2 max-w-[820px] text-[17px] text-muted">
        We looked at your resume and your undergraduate degree and picked out topics you may care about. Keep the ones that fit, remove the
        rest, or add your own.
      </p>

      {lists.loading && !lists.data && <div className="mt-8"><PageSkeleton /></div>}
      {lists.error && <div className="mt-8"><ErrorState message={lists.error} onRetry={lists.reload} /></div>}

      {lists.data && (
        <>
          <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-[1fr_520px]">
            <Card className="p-8">
              <h2 className="mb-5 text-[22px] font-semibold">Suggested from your background</h2>
              {actionError && <p role="alert" className="mb-3 rounded-control bg-danger-bg px-3 py-2 text-[14px] text-danger-text">{actionError}</p>}

              {background.length === 0 ? (
                <EmptyState>
                  No suggestions yet. Upload your resume or undergraduate transcript on the right, or add your own below.
                </EmptyState>
              ) : (
                <ul className="space-y-3">
                  {background.map((i) => (
                    <SuggestionCard key={i.interest_id} i={i} busy={busyId === i.interest_id} busyAction={busyId === i.interest_id ? busyAction : null} onAct={act} />
                  ))}
                </ul>
              )}

              <AddOwn own={own} onChanged={lists.reload} onRemove={(i) => act(i, 'remove')} busyId={busyId} />

              <div className="mt-8 rounded-card border border-dashed border-border bg-page px-5 py-4">
                <p className="flex items-center gap-3">
                  <span className="font-semibold">Favourite professors</span>
                  <Badge>Coming later</Badge>
                </p>
                <p className="mt-1 text-[14px] text-muted">Later you&apos;ll be able to pick professors you like, and we&apos;ll favour their courses.</p>
              </div>
            </Card>

            <div className="space-y-6">
              <LookedAt onUploaded={lists.reload} />
              <Card>
                <h2 className="mb-3 text-[20px] font-semibold">Your privacy</h2>
                <p className="text-[14px] text-muted">
                  Your resume is only used to suggest interests. Your advisor sees the interests you save, not your resume. You can change or
                  remove your saved interests on this page at any time.
                </p>
              </Card>
            </div>
          </div>

          <div className="mt-8 flex flex-wrap items-center gap-4">
            <Button className="h-14 px-6 text-[16px]" onClick={goOn}>
              {openTerm
                ? `Save ${keptCount} ${keptCount === 1 ? 'interest' : 'interests'} and build my schedule`
                : `Save ${keptCount} ${keptCount === 1 ? 'interest' : 'interests'}`}
            </Button>
            <Button variant="outline" className="h-14 px-6 text-[16px]" onClick={goOn}>
              Skip, use popular courses
            </Button>
            <p className="text-[14px] text-muted">Core courses are always scheduled first. Interests only affect electives.</p>
          </div>
        </>
      )}
    </PageShell>
  )
}

function SuggestionCard({ i, busy, busyAction, onAct }: { i: InterestView; busy: boolean; busyAction: 'confirm' | 'remove' | null; onAct: (i: InterestView, a: 'confirm' | 'remove') => void }) {
  const kept = i.status === 'confirmed'
  return (
    <li
      className={`flex items-center gap-4 rounded-card border px-5 py-4 ${kept ? 'border-primary/40 bg-primary-soft/50' : 'border-border bg-card'}`}
    >
      <div className="min-w-0 flex-1">
        <p className="font-semibold">{i.label}</p>
        <p className="text-[14px] text-muted">{SOURCE_LINE[i.source]}</p>
      </div>
      {kept ? (
        <Badge tone="success">Kept</Badge>
      ) : (
        <Button className="h-12" disabled={busy} onClick={() => onAct(i, 'confirm')}>
          {busyAction === 'confirm' ? 'Saving…' : 'Keep'}
        </Button>
      )}
      <Button variant="outline" className="h-12" disabled={busy} onClick={() => onAct(i, 'remove')}>
        {busyAction === 'remove' ? 'Removing…' : 'Remove'}
      </Button>
    </li>
  )
}

function AddOwn({ own, onChanged, onRemove, busyId }: { own: InterestView[]; onChanged: () => void; onRemove: (i: InterestView) => void; busyId: number | null }) {
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function add(e: FormEvent) {
    e.preventDefault()
    if (!label.trim()) return
    setBusy(true)
    setError(null)
    try {
      await api.post<InterestView>('/students/me/interests', { label })
      setLabel('')
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add that interest')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-8">
      <h3 className="mb-2 font-semibold">Add your own</h3>
      <form onSubmit={add} className="flex gap-3">
        <input
          className="h-12 flex-1 rounded-control border border-border bg-card px-4 focus:border-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
          placeholder="For example: AI agents, product management"
          aria-label="Add your own interest"
          maxLength={60}
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        <Button type="submit" variant="outline" className="h-12" disabled={busy || !label.trim()}>
          Add
        </Button>
      </form>
      {error && <p role="alert" className="mt-2 text-[14px] text-danger-text">{error}</p>}
      {own.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-2">
          {own.map((i) => (
            <li key={i.interest_id} className="flex items-center gap-2 rounded-full bg-primary-soft px-4 py-1.5 text-[14px] text-primary">
              {i.label} · added by you
              <button aria-label={`Remove ${i.label}`} disabled={busyId === i.interest_id} className="text-primary/70 hover:text-primary" onClick={() => onRemove(i)}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function LookedAt({ onUploaded }: { onUploaded: () => void }) {
  return (
    <Card>
      <h2 className="mb-4 text-[20px] font-semibold">What we looked at</h2>
      <div className="space-y-5">
        <UploadRow docType="resume" title="Resume" onUploaded={onUploaded} />
        <UploadRow docType="undergrad_transcript" title="Undergraduate degree" onUploaded={onUploaded} />
      </div>
    </Card>
  )
}

function UploadRow({ docType, title, onUploaded }: { docType: DocType; title: string; onUploaded?: () => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<UploadResult | null>(null)

  async function pick(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('doc_type', docType)
      setResult(await api.post<UploadResult>('/students/me/documents', form))
      onUploaded?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'The upload failed')
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }

  return (
    <div>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-medium">{title}</p>
          <p className="text-[14px] text-muted">
            {result ? `${result.filename ?? 'File'} · uploaded just now` : 'PDF or .txt, up to 5 MB'}
          </p>
        </div>
        <input
          ref={input}
          type="file"
          accept=".pdf,.txt,application/pdf,text/plain"
          className="sr-only"
          aria-label={`Upload ${title}`}
          onChange={(e) => pick(e.target.files?.[0])}
        />
        <button className="text-primary underline disabled:opacity-50" disabled={busy} onClick={() => input.current?.click()}>
          {result ? 'Replace' : 'Upload'}
        </button>
      </div>
      {busy && <p role="status" className="mt-2 text-[14px] text-muted">Reading your document… this can take up to a minute</p>}
      {error && <p role="alert" className="mt-2 rounded-control bg-danger-bg px-3 py-2 text-[14px] text-danger-text">{error}</p>}
      {result && (
        <p role="status" className="mt-2 text-[14px] text-success-text">
          {result.suggested.length > 0
            ? `Found ${result.suggested.length} new ${result.suggested.length === 1 ? 'interest' : 'interests'}. Check them on the left.`
            : 'No new interests found in this file.'}
        </p>
      )}
    </div>
  )
}
