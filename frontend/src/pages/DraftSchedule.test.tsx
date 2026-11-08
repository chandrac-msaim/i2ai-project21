/** The student schedule page for every status: the notice, the editing hints and the suggestions panel must follow the
 *  status (docs/UI_SPEC.md M8e). The API is mocked at fetch level, so the real page, hooks and client run. */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Dashboard, HistoryItem, ScheduleView, SuggestionGroups } from '../api/types'
import { AuthProvider } from '../auth'
import DraftSchedule from './DraftSchedule'

const ITEM = {
  units: '6.0', section_id: 's', days_times: 'M 18:30-21:20', modality: 'in_person', seats_left: 28, is_conditional: false,
  needs_approval: false, match_score: null, reason: null, in_ai_draft: true, ai_original_course: null, issues: [] as string[],
}
const CORE = { ...ITEM, course_code: '94-815', title: 'Agentic Technologies', item_type: 'core', is_locked: true, added_by: 'ai' }
const ELECTIVE = { ...ITEM, course_code: '94-830', title: 'AI, Ethics and Humanity', item_type: 'elective', is_locked: false, added_by: 'ai', match_score: 0.46, reason: 'Matches your interest.' }

function schedule(status: ScheduleView['status'], over: Partial<ScheduleView> = {}): ScheduleView {
  const draft = status === 'draft'
  return {
    schedule_id: `sched-${status}`, student_id: 'S0056', term_code: 'S27', term_label: 'Spring 2027', version: 2, status,
    previous_schedule_id: null, edited_by_student: false, advisor_comment: null, ai_explanation: 'Built from your plan.', load_rationale: '12 of 54 units.',
    total_units: '12.0', core_units: '6.0', elective_units: '6.0', max_units: 54, max_units_extended: 60, extended_units_min_qpa: '3.50',
    extended_allowed: false, items: [CORE, ELECTIVE], checks: [], blocking: [], valid: true, flags: [],
    submitted_at: ['submitted', 'approved', 'rejected'].includes(status) ? '2026-11-07T21:04:45-05:00' : null,
    decided_at: ['approved', 'rejected'].includes(status) ? '2026-11-08T10:15:00-05:00' : null,
    can_edit: draft, can_submit: draft, can_reset: false, can_discard: draft, can_withdraw: status === 'submitted',
    can_resubmit: status === 'rejected', ...over,
  } as unknown as ScheduleView
}

const TERM = { term_code: 'S27', term_label: 'Spring 2027', season: 'Spring', year: 2027, seq: 3, registration_opens: '2026-10-01', registration_closes: '2026-12-04', status: 'open', has_offerings: true }

function dashboard(confirmed: number): Dashboard {
  return {
    student: { student_id: 'S0056', first_name: 'Priya', last_name: 'Raman', university_name: 'Carnegie Mellon University', academic_terms: 3 },
    is_new_student: false, progress: { qpa: '3.59' }, advisor: { advisor_id: 'ADV001', name: 'Morgan Lee', email: 'm@x', title: 'Advisor' },
    interests: { suggested: [], confirmed: Array.from({ length: confirmed }, (_, i) => ({ interest_id: i + 1, label: 'AI governance' })) },
    unread_messages: 0, terms: { today: '2026-11-07', plannable: [TERM], upcoming: [], recent_closed: [] }, schedules: [],
  } as unknown as Dashboard
}

const SUGGESTIONS = {
  schedule_id: 'x', term_code: 'S27', term_label: 'Spring 2027', basis: 'popularity', interest_match: [],
  popular: [{ course_code: '95-767', title: 'Cybersecurity', units: '6.0', rank: 1, group_code: 'popular', matched_interest: null, final_score: 0.7, flags: [], reason: 'Popular.', in_schedule: false }],
} as unknown as SuggestionGroups

interface Setup {
  current: ScheduleView | null
  confirmed?: number
  history?: HistoryItem[]
  byId?: Record<string, ScheduleView>
}

function mockApi({ current, confirmed = 1, history = [], byId = {} }: Setup) {
  const reply = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const path = String(input).replace(/^\/api/, '')
    if (path.startsWith('/students/me/dashboard')) return reply(dashboard(confirmed))
    if (path.startsWith('/auth/me')) return reply({ university: null, programs: [] })
    if (path.startsWith('/messages/')) return reply({ student_id: 'S0056', unread: 0, messages: [] })
    if (path.startsWith('/schedules/current')) return reply(current)
    if (path.startsWith('/schedules/history')) return reply(history)
    const m = path.match(/^\/schedules\/([^/?]+)(\/suggestions)?/)
    if (m && m[2]) return reply(SUGGESTIONS)
    if (m && byId[m[1]]) return reply(byId[m[1]])
    return reply({})
  }))
}

function renderPage() {
  localStorage.setItem('graydi_token', 't')
  localStorage.setItem('graydi_user', JSON.stringify({ user_id: 'u', email: 'priya.raman@demo.graydi.local', role: 'student', student_id: 'S0056', advisor_id: null }))
  return render(
    <MemoryRouter initialEntries={['/schedule/S27']}>
      <AuthProvider>
        <Routes>
          <Route path="/schedule/:term" element={<DraftSchedule />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

const text = () => document.body.textContent ?? ''
const notice = async () => screen.findByTestId('schedule-notice')
const button = (name: string) => screen.queryByRole('button', { name })
/** Everything that invites the student to change the schedule. */
function expectNoEditing() {
  expect(screen.queryByText('Suggested electives')).toBeNull()
  expect(screen.queryByText('Search all electives')).toBeNull()
  expect(screen.queryByText('Tell us your interests')).toBeNull()
  expect(text()).not.toMatch(/Room for|units left|Full load/)
  expect(text()).not.toContain('Above 54 units needs a QPA above 3.50')
  expect(text()).not.toContain('Add, swap or remove electives')
  for (const name of ['Send to advisor', 'Swap', 'Remove', 'Undo', 'Discard draft', 'Build again', 'Reset to AI suggestion']) expect(button(name), name).toBeNull()
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('student schedule page follows the schedule status', () => {
  it('draft: amber notice, unit hints, suggestions, interests prompt and the editing buttons', async () => {
    mockApi({ current: schedule('draft'), confirmed: 0 })
    renderPage()
    const n = await notice()
    expect(n.dataset.status).toBe('draft')
    expect(n.textContent).toBe('This is a draft. Morgan Lee approves the final schedule and sees which changes you made.')
    expect(await screen.findByText('Suggested electives')).toBeTruthy()
    expect(screen.getByText('Search all electives')).toBeTruthy()
    expect(screen.getByText('Tell us your interests')).toBeTruthy()
    expect(text()).toMatch(/Room for one more/)
    expect(text()).toContain('Above 54 units needs a QPA above 3.50.')
    for (const name of ['Send to advisor', 'Swap', 'Remove', 'Discard draft', 'Build again']) expect(button(name), name).toBeTruthy()
  })

  it('submitted: blue notice with the date, read only, Withdraw available', async () => {
    mockApi({ current: schedule('submitted') })
    renderPage()
    const n = await notice()
    expect(n.dataset.status).toBe('submitted')
    expect(n.textContent).toBe('Sent to Morgan Lee on Nov 7, 2026. You can withdraw until a decision is made.')
    expect(text()).not.toContain('This is a draft')
    expectNoEditing()
    expect(button('Withdraw')).toBeTruthy()
    expect(text()).toContain('This schedule is read only.')
  })

  it('approved: green locked notice with the advisor comment and the units summary, nothing to edit', async () => {
    mockApi({ current: schedule('approved', { advisor_comment: 'Good plan. Let us meet in week 3.' }), confirmed: 0 })
    renderPage()
    const n = await notice()
    expect(n.dataset.status).toBe('approved')
    expect(n.textContent).toContain('Approved by Morgan Lee on Nov 8, 2026. This schedule is locked.')
    expect(n.textContent).toContain('Good plan. Let us meet in week 3.')
    expect(text()).not.toContain('This is a draft')
    expectNoEditing()
    expect(text()).toContain('12 of 54 units this term') // the units summary stays
    expect(text()).toContain('of 54 max')
    expect(button('Withdraw')).toBeNull()
    expect(button('Edit and resubmit')).toBeNull()
  })

  it('rejected: red notice with the comment and one Edit and resubmit button, no suggestions until a new draft exists', async () => {
    mockApi({ current: schedule('rejected', { advisor_comment: 'Please take 95-888 first.' }), confirmed: 0 })
    renderPage()
    const n = await notice()
    expect(n.dataset.status).toBe('rejected')
    expect(n.textContent).toContain('Not approved by Morgan Lee on Nov 8, 2026.')
    expect(n.textContent).toContain('Please take 95-888 first.')
    expect(screen.getAllByRole('button', { name: 'Edit and resubmit' })).toHaveLength(1)
    expect(text()).not.toContain('This is a draft')
    expectNoEditing()
    expect(text()).toContain('Use "Edit and resubmit" to continue.')
  })

  it.each([['superseded'], ['discarded']] as const)('%s version opened from the history: grey read-only notice, no editing controls', async (status) => {
    const current = schedule('draft')
    const old = schedule(status, { schedule_id: `old-${status}`, version: 1 })
    const history = [
      { schedule_id: current.schedule_id, term_code: 'S27', term_label: 'Spring 2027', version: 2, status: 'draft', courses: 2, total_units: '12.0', edited_by_student: false, created_at: '2026-11-08T09:00:00-05:00', advisor_comment: null },
      { schedule_id: old.schedule_id, term_code: 'S27', term_label: 'Spring 2027', version: 1, status, courses: 2, total_units: '12.0', edited_by_student: false, created_at: '2026-11-07T09:00:00-05:00', advisor_comment: null },
    ] as unknown as HistoryItem[]
    mockApi({ current, history, byId: { [old.schedule_id]: old } })
    renderPage()
    await notice()
    fireEvent.click(screen.getByRole('button', { name: 'Previous versions' }))
    const viewButtons = await screen.findAllByRole('button', { name: 'View' })
    expect(viewButtons).toHaveLength(1) // the shown version has no View button
    fireEvent.click(viewButtons[0])
    await waitFor(() => expect(screen.getByTestId('schedule-notice').dataset.status).toBe(status))
    expect(screen.getByTestId('schedule-notice').textContent).toContain('cannot be changed')
    expectNoEditing()
    expect(button('Edit and resubmit')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Back to the current schedule' }))
    await waitFor(() => expect(screen.getByTestId('schedule-notice').dataset.status).toBe('draft'))
  })

  it('no schedule yet: no notice at all, just the build card', async () => {
    mockApi({ current: null })
    renderPage()
    expect(await screen.findByText(/No schedule yet for Spring 2027/)).toBeTruthy()
    expect(screen.queryByTestId('schedule-notice')).toBeNull()
  })
})
