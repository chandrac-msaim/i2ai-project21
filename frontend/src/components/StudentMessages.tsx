import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import type { MessageView } from '../api/types'
import { useGet } from '../api/useApi'
import { Badge, Card, CardTitle, EmptyState, ErrorState } from './ui'

interface Thread {
  student_id: string
  messages: MessageView[]
  unread: number
}

/** The student's message thread with their advisor, read only (no chat). Opening it marks the advisor's messages as read on the
 *  server; this one view still shows them as "New" because the API returns the read state from before the call. */
export default function StudentMessages({ studentId, advisorName }: { studentId: string; advisorName?: string }) {
  const { data, error, loading, reload } = useGet<Thread>(`/messages/${studentId}`)
  const { hash } = useLocation()
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (hash === '#messages' && data) box.current?.scrollIntoView?.()
  }, [hash, data])

  return (
    <div id="messages" ref={box}>
      <Card>
        <CardTitle
          aside={data && data.unread > 0 ? <Badge tone="primary">{data.unread} new</Badge> : undefined}
        >
          Messages{advisorName ? ` from ${advisorName}` : ' from your advisor'}
        </CardTitle>
        {loading && !data && <p role="status" className="text-muted">Loading messages…</p>}
        {error && <ErrorState message={error} onRetry={reload} />}
        {data && data.messages.length === 0 && <EmptyState>No messages yet. Your advisor can write to you here.</EmptyState>}
        {data && data.messages.length > 0 && (
          <ul className="space-y-3" aria-label="Messages with your advisor">
            {data.messages.map((m) => (
              <li key={m.message_id} className={`rounded-control border border-border px-4 py-3 ${m.mine ? 'bg-page' : 'bg-card'}`}>
                <p className="whitespace-pre-wrap">{m.body}</p>
                <p className="mt-1 flex items-center gap-2 text-[13px] text-muted">
                  <span>{m.mine ? 'You' : m.sender_name}</span>
                  <span aria-hidden>·</span>
                  <time dateTime={m.sent_at}>{new Date(m.sent_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</time>
                  {!m.mine && m.read_at === null && <Badge tone="primary">New</Badge>}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}
