// The publishing day runs 06:45 to 06:45 UTC (07:45 to 07:45 WAT), as in
// model/engrebodds_model/live/tips.py publishing_window. Times are shown in WAT.

export const TZ = "Africa/Lagos";
const START_MIN = 6 * 60 + 45;
const DAY_MS = 86_400_000;

export interface Window {
  start: Date;
  end: Date;
  date: string; // yyyy-mm-dd of the UTC morning it starts, which is also its WAT date
}

export function windowFor(date: string): Window {
  const start = new Date(`${date}T06:45:00Z`);
  return { start, end: new Date(start.getTime() + DAY_MS), date };
}

export function publishingWindow(now: Date = new Date()): Window {
  const minutes = now.getUTCHours() * 60 + now.getUTCMinutes();
  const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  if (minutes < START_MIN) day.setUTCDate(day.getUTCDate() - 1);
  return windowFor(day.toISOString().slice(0, 10));
}

export function shiftDate(date: string, days: number): string {
  const d = new Date(`${date}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export function isDate(s: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(`${s}T00:00:00Z`));
}

const timeFmt = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, weekday: "short", day: "numeric", month: "short" });
const longFmt = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, weekday: "long", day: "numeric", month: "long", year: "numeric" });

export const watTime = (d: Date | string) => timeFmt.format(new Date(d));
export const watDay = (d: Date | string) => dayFmt.format(new Date(d));
export const watLong = (d: Date | string) => longFmt.format(new Date(d));
/** Label for a publishing day relative to today: Today, Tomorrow, Yesterday or the date. */
export function dayLabel(date: string, today: string): string {
  if (date === today) return "Today";
  if (date === shiftDate(today, 1)) return "Tomorrow";
  if (date === shiftDate(today, -1)) return "Yesterday";
  return watDay(`${date}T12:00:00Z`);
}
const dateFmt = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, day: "numeric", month: "short", year: "numeric" });
/** "11 Aug 2024": for results, where the year matters. */
export const watDate = (d: Date | string) => dateFmt.format(new Date(d));
