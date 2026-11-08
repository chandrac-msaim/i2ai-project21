import type { ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, homeFor, useAuth } from './auth'
import AdvisorCaseload from './pages/AdvisorCaseload'
import AdvisorReview from './pages/AdvisorReview'
import ConfirmInterests from './pages/ConfirmInterests'
import DraftSchedule from './pages/DraftSchedule'
import Login from './pages/Login'
import StudentDashboard from './pages/StudentDashboard'

type Role = 'student' | 'advisor'

function Protected({ role, children }: { role: Role; children: ReactNode }) {
  const { user } = useAuth()
  if (!user) return <Navigate to="/login" replace />
  // an advisor who opens a student URL (or the reverse) lands on their own home page
  const allowed = role === 'advisor' ? user.role === 'advisor' || user.role === 'admin' : user.role === 'student'
  return allowed ? <>{children}</> : <Navigate to={homeFor(user)} replace />
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Protected role="student"><StudentDashboard /></Protected>} />
          <Route path="/interests" element={<Protected role="student"><ConfirmInterests /></Protected>} />
          <Route
            path="/schedule/:term"
            element={<Protected role="student"><DraftSchedule /></Protected>}
          />
          <Route path="/advisor" element={<Protected role="advisor"><AdvisorCaseload /></Protected>} />
          <Route path="/advisor/schedules/:id" element={<Protected role="advisor"><AdvisorReview /></Protected>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
