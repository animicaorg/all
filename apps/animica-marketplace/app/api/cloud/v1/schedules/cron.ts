// Minimal, correct 5-field cron for CloudSchedule (UTC): minute hour day-of-month month day-of-week.
//
// Supported syntax per field: "*", numbers, lists "a,b,c", ranges "a-b", steps "*/n" and
// "a-b/n". Numbers only (no month/day names). Day-of-week accepts 0-7 with 7 == Sunday == 0.
// Standard Vixie-cron day semantics: when BOTH day-of-month and day-of-week are restricted
// (i.e. neither starts with "*"), a date matches when EITHER matches.
//
// This exists so the schedules API can (a) reject an invalid expression at write time with a
// real reason, (b) persist a truthful nextRunAt for the scheduler worker, and (c) enforce the
// plan's minimum-interval floor against the expression's ACTUAL firing cadence.

export interface CronSpec {
  minutes: Set<number>;
  hours: Set<number>;
  dom: Set<number>;
  months: Set<number>;
  dow: Set<number>; // normalized: 7 -> 0
  domRestricted: boolean;
  dowRestricted: boolean;
}

const FIELD_RE = /^(\*|\d{1,2}(-\d{1,2})?)(\/\d{1,3})?$/;

function parseField(field: string, min: number, max: number, label: string): { values: Set<number>; restricted: boolean } {
  const values = new Set<number>();
  const restricted = !field.startsWith('*');
  for (const part of field.split(',')) {
    if (!FIELD_RE.test(part)) throw new Error(`invalid cron ${label} field "${part}"`);
    const [rangePart, stepPart] = part.split('/');
    const step = stepPart != null ? Number(stepPart) : 1;
    if (!Number.isInteger(step) || step < 1) throw new Error(`invalid cron step in "${part}"`);
    let lo: number;
    let hi: number;
    if (rangePart === '*') {
      lo = min;
      hi = max;
    } else if (rangePart.includes('-')) {
      const [a, b] = rangePart.split('-').map(Number);
      lo = a;
      hi = b;
    } else {
      lo = Number(rangePart);
      hi = stepPart != null ? max : lo; // "a/n" means "from a to max, every n" in Vixie cron
    }
    if (lo < min || hi > max || lo > hi) {
      throw new Error(`cron ${label} value out of range in "${part}" (allowed ${min}-${max})`);
    }
    for (let v = lo; v <= hi; v += step) values.add(v);
  }
  if (values.size === 0) throw new Error(`cron ${label} field matches nothing`);
  return { values, restricted };
}

export function parseCron(expr: string): CronSpec {
  const clean = String(expr ?? '').trim().replace(/\s+/g, ' ');
  if (!clean || clean.length > 100) throw new Error('cron expression must be 1-100 characters');
  const fields = clean.split(' ');
  if (fields.length !== 5) {
    throw new Error('cron must have exactly 5 fields: minute hour day-of-month month day-of-week');
  }
  const minutes = parseField(fields[0], 0, 59, 'minute');
  const hours = parseField(fields[1], 0, 23, 'hour');
  const dom = parseField(fields[2], 1, 31, 'day-of-month');
  const months = parseField(fields[3], 1, 12, 'month');
  const dowRaw = parseField(fields[4], 0, 7, 'day-of-week');
  const dow = new Set<number>([...dowRaw.values].map((v) => (v === 7 ? 0 : v)));
  return {
    minutes: minutes.values,
    hours: hours.values,
    dom: dom.values,
    months: months.values,
    dow,
    domRestricted: dom.restricted,
    dowRestricted: dowRaw.restricted,
  };
}

function dayMatches(spec: CronSpec, d: Date): boolean {
  const domOk = spec.dom.has(d.getUTCDate());
  const dowOk = spec.dow.has(d.getUTCDay());
  if (spec.domRestricted && spec.dowRestricted) return domOk || dowOk;
  if (spec.domRestricted) return domOk;
  if (spec.dowRestricted) return dowOk;
  return true;
}

/** The next UTC fire time strictly after `from`, or null if none within 366 days
 *  (e.g. "0 0 30 2 *" never fires). Skips whole months/days/hours, so it is fast. */
export function nextCronRun(spec: CronSpec, from: Date): Date | null {
  let t = Math.floor(from.getTime() / 60_000) * 60_000 + 60_000; // next whole minute
  const limit = from.getTime() + 366 * 86_400_000;
  while (t <= limit) {
    const d = new Date(t);
    if (!spec.months.has(d.getUTCMonth() + 1)) {
      t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
      continue;
    }
    if (!dayMatches(spec, d)) {
      t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + 1);
      continue;
    }
    if (!spec.hours.has(d.getUTCHours())) {
      t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), d.getUTCHours() + 1);
      continue;
    }
    if (!spec.minutes.has(d.getUTCMinutes())) {
      t += 60_000;
      continue;
    }
    return d;
  }
  return null;
}

/**
 * The smallest gap (in minutes) between successive fire times, sampled over the next
 * `samples` occurrences. Used to enforce the plan's schedule floor against what the
 * expression will actually do — "0,1 * * * *" is a 1-minute cadence no matter how the
 * rest of the expression looks.
 */
export function minGapMinutes(spec: CronSpec, from: Date, samples = 6): number | null {
  let prev = nextCronRun(spec, from);
  if (!prev) return null;
  let min = Infinity;
  for (let i = 0; i < samples; i++) {
    const next = nextCronRun(spec, prev);
    if (!next) break; // fires so rarely there is no next sample — no floor concern
    min = Math.min(min, (next.getTime() - prev.getTime()) / 60_000);
    prev = next;
  }
  return Number.isFinite(min) ? min : null;
}
