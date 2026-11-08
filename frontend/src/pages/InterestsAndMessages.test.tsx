/** POLISH #8 (Keep updates the page without a reload) and POLISH #6 (the student reads the advisor's messages).
 *  The API is mocked at fetch level, so the real pages, hooks and client run. */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { InterestLists, InterestView, MessageView } from '../api/types'
import { AuthProvider } from '../auth'
import StudentMessages from '../components/StudentMessages'
import ConfirmInterests, { applyInterest } from './ConfirmInterests'

const interest = (id: number, label: string, status: InterestView['status'] = 'suggested'): InterestView => ({
  interest_id: id, label, source: 'resume', status, created_at: '2026-11-08T02:00:00Z', confirmed_at: status === 'confirmed' ? '2026-11-08T02:01:00Z' : null,
})

function login() {
  localStorage.setItem('graydi_token', 't')
  localStorage.setItem('graydi_user', JSON.stringify({ user_id: 'u', email: 's@demo.graydi.local', role: 'student', student_id: 'S0127', advisor_id: null }))
}
const reply = (body: unknown) => ({ ok: true, status: 200, json: async () => body })

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('applyInterest', () => {
  const lists: InterestLists = { suggested: [interest(1, 'A'), interest(2, 'B')], confirmed: [interest(3, 'C', 'confirmed')] }
  it('moves a kept interest to confirmed', () => {
    const out = applyInterest(lists, interest(1, 'A', 'confirmed'))
    expect(out.suggested.map((i) => i.label)).toEqual(['B'])
    expect(out.confirmed.map((i) => i.label).sort()).toEqual(['A', 'C'])
  })
  it('drops a removed interest from both lists', () => {
    const out = applyInterest(lists, interest(3, 'C', 'removed'))
    expect(out.confirmed).toEqual([])
    expect(out.suggested).toHaveLength(2)
  })
})

describe('Keep on the interests page', () => {
  it('shows Kept and the new count from the response, from the PATCH response alone (no refetch needed)', async () => {
    login()
    const stale: InterestLists = { suggested: [interest(1, 'Data governance'), interest(2, 'Ethical AI')], confirmed: [] }
    vi.stubGlobal('fetch', vi.fn(async (input: string, init?: { method?: string }) => {
      const path = String(input).replace(/^\/api/, '')
      if (path.startsWith('/students/me/interests/1') && init?.method === 'PATCH') return reply(interest(1, 'Data governance', 'confirmed'))
      if (path.startsWith('/students/me/interests')) return reply(stale) // a refetch would still return the old list: the page must not need one
      if (path.startsWith('/terms/plannable')) return reply({ plannable: [], upcoming: [], recent_closed: [] })
      if (path.startsWith('/students/me/dashboard')) return reply({ student: { first_name: 'Selin', last_name: 'Aksoy' } })
      return reply({})
    }))
    render(
      <MemoryRouter initialEntries={['/interests']}>
        <AuthProvider>
          <Routes>
            <Route path="/interests" element={<ConfirmInterests />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    )
    expect(await screen.findByText('Save 0 interests', {}, { timeout: 5000 })).toBeTruthy()
    fireEvent.click((await screen.findAllByRole('button', { name: 'Keep' }))[0])
    await waitFor(() => expect(screen.getByText('Kept')).toBeTruthy(), { timeout: 5000 })
    expect(screen.getByText('Save 1 interest')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: 'Keep' })).toHaveLength(1) // only the other one can still be kept
  })
})

describe('Keep while the request runs', () => {
  it('shows Saving… on the button, disables it and sends one request even if clicked twice', async () => {
    login()
    let release: (v: unknown) => void = () => {}
    const patches: string[] = []
    const lists: InterestLists = { suggested: [interest(1, 'Data governance')], confirmed: [] }
    vi.stubGlobal('fetch', vi.fn(async (input: string, init?: { method?: string }) => {
      const path = String(input).replace(/^\/api/, '')
      if (init?.method === 'PATCH') {
        patches.push(path)
        await new Promise((r) => (release = r)) // the server is slow (model still loading)
        return reply(interest(1, 'Data governance', 'confirmed'))
      }
      if (path.startsWith('/students/me/interests')) return reply(lists)
      if (path.startsWith('/terms/plannable')) return reply({ plannable: [], upcoming: [], recent_closed: [] })
      if (path.startsWith('/students/me/dashboard')) return reply({ student: { first_name: 'Selin', last_name: 'Aksoy' } })
      return reply({})
    }))
    render(
      <MemoryRouter initialEntries={['/interests']}>
        <AuthProvider>
          <Routes>
            <Route path="/interests" element={<ConfirmInterests />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    )
    const keep = await screen.findByRole('button', { name: 'Keep' }, { timeout: 5000 })
    fireEvent.click(keep)
    fireEvent.click(keep) // a fast second click before React re-renders
    const saving = await screen.findByRole('button', { name: 'Saving…' })
    expect((saving as HTMLButtonElement).disabled).toBe(true)
    expect(patches).toHaveLength(1)
    release(undefined)
    await waitFor(() => expect(screen.getByText('Kept')).toBeTruthy(), { timeout: 5000 })
    expect(patches).toHaveLength(1)
  })
})

describe('the student reads the advisor messages', () => {
  const msg = (over: Partial<MessageView>): MessageView => ({
    message_id: 1, sender_role: 'advisor', sender_name: 'Morgan Lee', body: 'Welcome. Let us meet in week 1.',
    sent_at: '2026-11-08T07:05:00Z', read_at: null, schedule_id: null, mine: false, ...over,
  })
  const mount = (thread: unknown) => {
    login()
    const fetchMock = vi.fn(async () => reply(thread))
    vi.stubGlobal('fetch', fetchMock)
    render(
      <MemoryRouter>
        <StudentMessages studentId="S0127" advisorName="Morgan Lee" />
      </MemoryRouter>,
    )
    return fetchMock
  }

  it('lists the thread, marks the unread advisor message as new and says how many', async () => {
    const f = mount({ student_id: 'S0127', unread: 1, messages: [msg({}), msg({ message_id: 2, mine: true, sender_role: 'student', sender_name: 'Selin Aksoy', body: 'Thank you.', read_at: null })] })
    expect(await screen.findByText('Welcome. Let us meet in week 1.')).toBeTruthy()
    expect(screen.getByText('Thank you.')).toBeTruthy()
    expect(screen.getByText('1 new')).toBeTruthy()
    expect(screen.getAllByText('New')).toHaveLength(1) // only the advisor's message, not her own
    expect(screen.getByText('Messages from Morgan Lee')).toBeTruthy()
    expect(f).toHaveBeenCalledWith('/api/messages/S0127', expect.anything())
  })

  it('does not show New for messages that were already read', async () => {
    mount({ student_id: 'S0127', unread: 0, messages: [msg({ read_at: '2026-11-08T08:00:00Z' })] })
    expect(await screen.findByText('Welcome. Let us meet in week 1.')).toBeTruthy()
    expect(screen.queryByText('New')).toBeNull()
    expect(screen.queryByText(/ new$/)).toBeNull()
  })

  it('shows an empty state without messages', async () => {
    mount({ student_id: 'S0127', unread: 0, messages: [] })
    expect(await screen.findByText(/No messages yet/)).toBeTruthy()
  })
})
