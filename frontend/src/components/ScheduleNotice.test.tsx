import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ScheduleNotice, { type ScheduleStatus } from './ScheduleNotice'

afterEach(cleanup)

const base = { advisorName: 'Morgan Lee', submittedAt: '2026-11-07T21:04:45-05:00', decidedAt: '2026-11-08T10:15:00-05:00' }
const notice = () => screen.getByTestId('schedule-notice')

describe('ScheduleNotice: one component, driven by the status', () => {
  it('draft: amber, names the advisor, no date', () => {
    render(<ScheduleNotice status="draft" {...base} />)
    expect(notice().dataset.status).toBe('draft')
    expect(notice().textContent).toBe('This is a draft. Morgan Lee approves the final schedule and sees which changes you made.')
    expect(notice().className).toContain('bg-warning-bg')
  })

  it('submitted: blue, the date it was sent and the withdraw hint', () => {
    render(<ScheduleNotice status="submitted" {...base} />)
    expect(notice().textContent).toBe('Sent to Morgan Lee on Nov 7, 2026. You can withdraw until a decision is made.')
    expect(notice().className).toContain('bg-info-bg')
    expect(notice().textContent).not.toContain('draft')
  })

  it('approved: green, the decision date, locked, and the advisor comment if there is one', () => {
    const { rerender } = render(<ScheduleNotice status="approved" {...base} advisorComment="Good plan. Let us meet in week 3." />)
    expect(notice().textContent).toContain('Approved by Morgan Lee on Nov 8, 2026. This schedule is locked.')
    expect(notice().textContent).toContain('Good plan. Let us meet in week 3.')
    expect(notice().className).toContain('bg-success-bg')
    expect(notice().textContent).not.toContain('This is a draft')
    rerender(<ScheduleNotice status="approved" {...base} advisorComment="  " />)
    expect(notice().textContent).not.toContain('Comment from')
  })

  it('rejected: red, the comment and the Edit and resubmit button', () => {
    const onResubmit = vi.fn()
    render(<ScheduleNotice status="rejected" {...base} advisorComment="Take 95-888 first." canResubmit onResubmit={onResubmit} />)
    expect(notice().textContent).toContain('Not approved by Morgan Lee on Nov 8, 2026.')
    expect(notice().textContent).toContain('Take 95-888 first.')
    expect(notice().className).toContain('bg-danger-bg')
    fireEvent.click(screen.getByRole('button', { name: 'Edit and resubmit' }))
    expect(onResubmit).toHaveBeenCalledOnce()
  })

  it('rejected without a comment says so, and without canResubmit there is no button', () => {
    render(<ScheduleNotice status="rejected" {...base} canResubmit={false} />)
    expect(notice().textContent).toContain('No comment was left.')
    expect(screen.queryByRole('button', { name: 'Edit and resubmit' })).toBeNull()
  })

  it('the button appears for a rejected schedule only', () => {
    const others: ScheduleStatus[] = ['draft', 'submitted', 'approved', 'discarded', 'superseded']
    for (const status of others) {
      const { unmount } = render(<ScheduleNotice status={status} {...base} canResubmit />)
      expect(screen.queryByRole('button', { name: 'Edit and resubmit' }), status).toBeNull()
      unmount()
    }
  })

  it.each([
    ['discarded', 'You discarded this draft. It stays in your history and cannot be changed.'],
    ['superseded', 'A newer version replaced this one. It stays in your history and cannot be changed.'],
  ] as const)('%s: grey, read only', (status, text) => {
    render(<ScheduleNotice status={status} {...base} />)
    expect(notice().textContent).toBe(text)
    expect(notice().className).toContain('bg-page')
  })

  it('an older version offers a way back to the current schedule', () => {
    const onBack = vi.fn()
    render(<ScheduleNotice status="superseded" {...base} onBack={onBack} />)
    fireEvent.click(screen.getByRole('button', { name: 'Back to the current schedule' }))
    expect(onBack).toHaveBeenCalledOnce()
  })

  it('falls back to "your advisor" when the name is unknown', () => {
    render(<ScheduleNotice status="draft" />)
    expect(notice().textContent).toContain('This is a draft. Your advisor approves')
  })
})
