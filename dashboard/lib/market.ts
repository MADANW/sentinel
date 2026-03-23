/**
 * market.ts — NYSE market hours helpers.
 *
 * All functions use UTC arithmetic — no browser-side Date.toLocaleString()
 * hacks, safe for SSR. Does not account for early closes or NYSE holidays
 * (acceptable for a status indicator; not a trading gate).
 *
 * NYSE regular hours: 09:30–16:00 Eastern Time, Monday–Friday.
 * Eastern Time offsets:
 *   EST (standard): UTC−5  (November → second Sunday in March)
 *   EDT (daylight):  UTC−4  (second Sunday in March → first Sunday in November)
 */

/**
 * Returns true if the current UTC time falls within NYSE regular trading hours.
 * Monday–Friday, 09:30–16:00 ET.
 */
export function isMarketOpen(now: Date = new Date()): boolean {
  const etOffset = getEasternOffsetHours(now)   // -4 or -5
  const etMs = now.getTime() + etOffset * 60 * 60 * 1000
  const etDate = new Date(etMs)

  const dayOfWeek = etDate.getUTCDay()  // 0=Sun, 1=Mon, ..., 5=Fri, 6=Sat
  if (dayOfWeek === 0 || dayOfWeek === 6) return false

  const hours = etDate.getUTCHours()
  const minutes = etDate.getUTCMinutes()
  const totalMinutes = hours * 60 + minutes

  const openMinutes  = 9 * 60 + 30   // 09:30
  const closeMinutes = 16 * 60        // 16:00

  return totalMinutes >= openMinutes && totalMinutes < closeMinutes
}

/**
 * Format an ISO timestamp string as HH:MM:SS ET.
 * Includes timezone label (ET) — does not distinguish EST/EDT.
 */
export function formatETTime(iso: string): string {
  const date = new Date(iso)
  const etOffset = getEasternOffsetHours(date)
  const etMs = date.getTime() + etOffset * 60 * 60 * 1000
  const etDate = new Date(etMs)

  const hh = String(etDate.getUTCHours()).padStart(2, '0')
  const mm = String(etDate.getUTCMinutes()).padStart(2, '0')
  const ss = String(etDate.getUTCSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss} ET`
}

/**
 * Returns the Eastern Time UTC offset in hours: -4 (EDT) or -5 (EST).
 *
 * DST rules (US):
 *   Starts: second Sunday of March at 02:00 local (clocks spring forward)
 *   Ends:   first Sunday of November at 02:00 local (clocks fall back)
 */
function getEasternOffsetHours(date: Date): -4 | -5 {
  const year = date.getUTCFullYear()
  const dstStart = nthSundayOfMonth(year, 2, 2)  // March (month=2), 2nd Sunday
  const dstEnd   = nthSundayOfMonth(year, 10, 1) // November (month=10), 1st Sunday

  // DST starts at 07:00 UTC (= 02:00 EST) and ends at 06:00 UTC (= 02:00 EDT)
  const dstStartUtc = new Date(Date.UTC(year, 2, dstStart, 7, 0, 0))
  const dstEndUtc   = new Date(Date.UTC(year, 10, dstEnd, 6, 0, 0))

  if (date >= dstStartUtc && date < dstEndUtc) return -4  // EDT
  return -5  // EST
}

/**
 * Returns the day-of-month of the nth Sunday in a given UTC month.
 * month: 0-indexed (0=Jan, 2=Mar, 10=Nov).
 */
function nthSundayOfMonth(year: number, month: number, n: number): number {
  // Find the first Sunday of the month
  const firstDay = new Date(Date.UTC(year, month, 1))
  const firstSunday = (7 - firstDay.getUTCDay()) % 7
  return 1 + firstSunday + (n - 1) * 7
}
