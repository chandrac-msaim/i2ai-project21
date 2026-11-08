import type { ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'
import { Badge } from './ui'

function LogoTile() {
  return (
    <span className="flex h-10 w-10 items-center justify-center rounded-control bg-primary text-white">
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M3 17l6-6 4 4 8-8" />
      </svg>
    </span>
  )
}

const pill = ({ isActive }: { isActive: boolean }) =>
  `rounded-control px-4 py-2 text-[15px] font-medium ${isActive ? 'bg-primary-soft text-primary' : 'text-ink hover:bg-page'}`

/** White top bar for students. Nav: Dashboard · Schedule · Interests (no "AI advisor": chat is deferred). */
export function StudentTopBar({ name, scheduleTo }: { name?: string; scheduleTo?: string }) {
  const { logout } = useAuth()
  const navigate = useNavigate()
  return (
    <header className="border-b border-border bg-card">
      <div className="mx-auto flex h-20 max-w-page items-center gap-8 px-10">
        <div className="flex items-center gap-3">
          <LogoTile />
          <span className="text-[18px] font-semibold">Gray DI Degree Pathway Advisor</span>
        </div>
        <nav className="flex items-center gap-1" aria-label="Main">
          <NavLink to="/" end className={pill}>
            Dashboard
          </NavLink>
          {/* the schedule page is built in M7b; before a term is chosen the link goes to the picker on the dashboard */}
          <NavLink to={scheduleTo ?? '/'} className={({ isActive }) => pill({ isActive: isActive && !!scheduleTo })}>
            Schedule
          </NavLink>
          <NavLink to="/interests" className={pill}>
            Interests
          </NavLink>
        </nav>
        <div className="ml-auto flex items-center gap-3">
          {name && <span>{name}</span>}
          <Badge>Student</Badge>
          <button
            className="h-11 rounded-control border border-border px-5 font-medium hover:bg-page"
            onClick={() => {
              logout()
              navigate('/login')
            }}
          >
            Log out
          </button>
        </div>
      </div>
    </header>
  )
}

/** Dark top bar for advisors. Counts for Reviews / Messages come with the advisor pages (M8). */
export function AdvisorTopBar({ name, title, reviews, messages }: { name?: string; title?: string; reviews?: number; messages?: number }) {
  const { logout } = useAuth()
  const navigate = useNavigate()
  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded-control px-4 py-2 text-[15px] font-medium text-white ${isActive ? 'bg-white/15' : 'hover:bg-white/10'}`
  return (
    <header className="bg-primary-dark text-white">
      <div className="mx-auto flex h-20 max-w-page items-center gap-8 px-10">
        <div className="flex items-center gap-3">
          <LogoTile />
          <span className="text-[18px] font-semibold">Gray DI Degree Pathway Advisor · Advisor</span>
        </div>
        <nav className="flex items-center gap-1" aria-label="Main">
          <NavLink to="/advisor" end className={link}>
            Caseload
          </NavLink>
          {/* the filtered caseload is the list of reviews and of unread messages (no separate pages in the MVP) */}
          <NavLink to="/advisor?filter=review" className={() => 'rounded-control px-4 py-2 text-[15px] font-medium text-white hover:bg-white/10'}>
            Reviews{reviews != null ? ` (${reviews})` : ''}
          </NavLink>
          <NavLink to="/advisor?filter=unread" className={() => 'rounded-control px-4 py-2 text-[15px] font-medium text-white hover:bg-white/10'}>
            Messages{messages != null ? ` (${messages})` : ''}
          </NavLink>
        </nav>
        <div className="ml-auto flex items-center gap-4">
          {name && <span>{name}{title ? ` · ${title}` : ''}</span>}
          <button
            className="h-10 rounded-control border border-white/30 px-4 font-medium hover:bg-white/10"
            onClick={() => {
              logout()
              navigate('/login')
            }}
          >
            Log out
          </button>
        </div>
      </div>
    </header>
  )
}

/** Footer after login: university, college and program, read from the logged-in user's program (never hard-coded). */
function PageFooter() {
  const { profile } = useAuth()
  if (!profile?.university) return null
  const colleges = [...new Set(profile.programs.map((p) => p.college))]
  const titles = profile.programs.map((p) => p.title)
  return (
    <footer className="border-t border-border bg-card">
      <div className="mx-auto flex max-w-page flex-wrap items-center justify-between gap-x-8 gap-y-1 px-10 py-5 text-[14px] text-muted">
        <span className="font-medium text-ink">{profile.university.name}</span>
        <span>{[...colleges, ...titles].join(' · ')}</span>
      </div>
    </footer>
  )
}

export function PageShell({ top, children }: { top: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-page">
      {top}
      <main className="mx-auto w-full max-w-page flex-1 px-10 pb-16 pt-10">{children}</main>
      <PageFooter />
    </div>
  )
}
