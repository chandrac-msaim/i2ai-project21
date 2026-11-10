export function fmtDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

/** "6 units"; "1 unit" */
export const unitsLabel = (n: number) => `${Number.isInteger(n) ? n : n.toFixed(1)} ${n === 1 ? 'unit' : 'units'}`

export const pct = (part: number, whole: number) => (whole > 0 ? Math.round((part / whole) * 100) : 0)

/** "1 seat left" / "3 seats left". */
export const seatsLabel = (n: number): string => `${n} ${n === 1 ? 'seat' : 'seats'} left`

/** Delivery mode from the data ("in_person" -> "in person"). */
export const modalityLabel = (m: string | null | undefined): string | null => (m ? m.replace(/_/g, ' ') : null)

const n = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1))

/**
 * "Units this term" line for a schedule. The AI writes load_rationale once, when it builds the draft; after the
 * student adds or swaps courses its numbers no longer describe the schedule. So the first sentence is always built
 * from the live totals, and the AI's text is kept only while it still matches (its extra sentences explain why the
 * draft is below the limit). When it no longer matches it is returned as `aiDraft`, to show as the AI's original.
 */
export function loadSummary(s: {
  total_units: number | string
  core_units: number | string
  elective_units: number | string
  max_units: number
  load_rationale: string | null
}): { current: string; aiDraft: string | null } {
  const total = Number(s.total_units)
  const core = Number(s.core_units)
  const electives = Number(s.elective_units)
  const current = `${n(total)} of ${n(s.max_units)} units: ${n(core)} core + ${n(electives)} electives.`
  const r = s.load_rationale?.trim()
  if (!r) return { current, aiDraft: null }
  const m = /^(\S+) of (\S+) units: (\S+) core \+ (\S+) electives\./.exec(r)
  const matches = m && Number(m[1]) === total && Number(m[2]) === s.max_units && Number(m[3]) === core && Number(m[4]) === electives
  return matches ? { current: r, aiDraft: null } : { current, aiDraft: r }
}
