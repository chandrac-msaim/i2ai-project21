import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import AdvisorReview from './AdvisorReview'

vi.mock('../auth', () => ({ useAuth: () => ({ user: { role: 'advisor' } }) }))
vi.mock('../components/TopBar', () => ({
  AdvisorTopBar: () => null,
  PageShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))
vi.mock('../api/useApi', () => ({ useGet: () => ({
  loading: false, reload: vi.fn(),
  data: {
    student: { first_name: 'Priya', last_name: 'Raman', student_id: 'S0056', program: {}, qpa: '3.59', units_completed: '24' },
    schedule: { status: 'submitted', total_units: '54', core_units: '30', elective_units: '24', max_units: 54,
      term_label: 'Spring 2027', version: 1, load_rationale: '48 of 54 units: 30 core + 18 electives.', checks: [], flags: [], items: [], blocking: [] },
    load_rationale: '48 of 54 units: 30 core + 18 electives.',
    ai_explanation: 'Original draft: 48 units.',
    messages: [], changes: [], removed_from_ai_draft: [],
  },
}) }))
afterEach(cleanup)
it('shows current units after editing while retaining the original AI explanation', () => {
  render(<MemoryRouter><AdvisorReview /></MemoryRouter>)
  const summary = screen.getByText('Units this term:').parentElement!
  expect(summary.textContent).toContain('54 of 54 units: 30 core + 24 electives.')
  expect(summary.textContent).not.toContain('48')
  expect(screen.getByText('Original draft: 48 units.')).toBeTruthy()
  expect(screen.getByText('When the AI built the draft: 48 of 54 units: 30 core + 18 electives.')).toBeTruthy()
})
