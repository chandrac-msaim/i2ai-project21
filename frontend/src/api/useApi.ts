import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from './client'

export interface ApiState<T> {
  data: T | null
  error: string | null
  status: number | null // HTTP status of the failed request (403 = not allowed)
  loading: boolean
  reload: () => void
  setData: (data: T) => void
}

/** GET `path` on mount and whenever `reload()` is called. */
export function useGet<T>(path: string): ApiState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .get<T>(path)
      .then((d) => {
        if (cancelled) return
        setData(d)
        setError(null)
        setStatus(null)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setError(e instanceof ApiError ? e.message : 'Unexpected error')
        setStatus(e instanceof ApiError ? e.status : null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [path, tick])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data, error, status, loading, reload, setData }
}
