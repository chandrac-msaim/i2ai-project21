import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, setUnauthorizedHandler, tokenStore, userStore } from './api/client'
import type { CurrentUser, LoginResponse, Profile } from './api/types'

interface AuthState {
  user: CurrentUser | null
  profile: Profile | null // university and program of the logged-in user (null until loaded, and for admins)
  login: (email: string, password: string) => Promise<CurrentUser>
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(() => (tokenStore.get() ? userStore.get<CurrentUser>() : null))

  const [profile, setProfile] = useState<Profile | null>(null)

  useEffect(() => {
    if (!user) {
      setProfile(null)
      return
    }
    let cancelled = false
    api
      .get<Profile>('/auth/me')
      .then((p) => !cancelled && setProfile({ university: p.university, programs: p.programs }))
      .catch(() => !cancelled && setProfile(null))
    return () => {
      cancelled = true
    }
  }, [user])

  const logout = useCallback(() => {
    tokenStore.clear()
    setUser(null)
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const res = await api.post<LoginResponse>('/auth/login', { email, password })
    tokenStore.set(res.access_token)
    userStore.set(res.user)
    setUser(res.user)
    return res.user
  }, [])

  useEffect(() => setUnauthorizedHandler(logout), [logout])

  const value = useMemo(() => ({ user, profile, login, logout }), [user, profile, login, logout])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}

/** Where each role lands after login (UI_SPEC: student -> /, advisor -> /advisor). */
export const homeFor = (user: CurrentUser) => (user.role === 'student' ? '/' : '/advisor')
