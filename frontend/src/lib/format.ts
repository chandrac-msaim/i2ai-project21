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
