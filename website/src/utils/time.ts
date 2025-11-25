/**
 * Time utilities: relative time ("3m ago", "in 2h") and ETA helpers.
 * Dependency-free, works in browser or Node runtimes.
 */

type Dateish = Date | number | string;

export type RelativeStyle = 'short' | 'long' | 'narrow';

export interface RelativeOpts {
  now?: number | Date;     // reference "now" (default: Date.now())
  style?: RelativeStyle;   // default: 'short'
}

export interface DurationOpts {
  style?: RelativeStyle;   // default: 'short'
  maxUnits?: number;       // how many components to show (default 2 → "1h 32m")
  smallestUnit?: 'millisecond' | 'second' | 'minute' | 'hour' | 'day';
  round?: 'floor' | 'ceil' | 'nearest'; // rounding mode for the smallest unit (default 'nearest')
  spacer?: ' ' | '';       // space between value and unit (default ''); ' ' for "1 h"
}

/* ------------------------------- Internals -------------------------------- */

const SEC = 1000;
const MIN = 60 * SEC;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;
const MONTH = 30 * DAY;   // calendar-approx for relative display
const YEAR = 365 * DAY;   // calendar-approx for relative display

const ORDERED: Array<{ unit: Intl.RelativeTimeFormatUnit; ms: number; short: string; narrow: string; long: string }> = [
  { unit: 'year',  ms: YEAR,  short: 'yr',  narrow: 'y',  long: 'year'  },
  { unit: 'month', ms: MONTH, short: 'mo',  narrow: 'mo', long: 'month' },
  { unit: 'week',  ms: WEEK,  short: 'wk',  narrow: 'w',  long: 'week'  },
  { unit: 'day',   ms: DAY,   short: 'd',   narrow: 'd',  long: 'day'   },
  { unit: 'hour',  ms: HOUR,  short: 'h',   narrow: 'h',  long: 'hour'  },
  { unit: 'minute',ms: MIN,   short: 'm',   narrow: 'm',  long: 'minute'},
  { unit: 'second',ms: SEC,   short: 's',   narrow: 's',  long: 'second'},
];

function toMillis(d: Dateish): number {
  if (d instanceof Date) return d.getTime();
  if (typeof d === 'number') return d;
  const t = Date.parse(d);
  if (Number.isNaN(t)) throw new Error(`Invalid date: ${String(d)}`);
  return t;
}

function getNowMs(now?: number | Date): number {
  if (now instanceof Date) return now.getTime();
  if (typeof now === 'number') return now;
  return Date.now();
}

function plural(n: number, singular: string): string {
  return n === 1 ? singular : `${singular}s`;
}

/* ---------------------------- Relative Time API --------------------------- */

/**
 * Human relative time like "3m ago" / "in 2h" (short by default).
 * Uses Intl.RelativeTimeFormat when available; otherwise falls back to compact strings.
 */
export function relativeTime(input: Dateish, opts: RelativeOpts = {}): string {
  const ref = getNowMs(opts.now);
  const target = toMillis(input);
  const diff = target - ref;
  const abs = Math.abs(diff);

  // Choose the largest unit under the magnitude
  const entry = ORDERED.find(e => abs >= e.ms) ?? ORDERED[ORDERED.length - 1];
  // Avoid 0 for future/past small diffs by rounding toward zero but at least 1 if non-zero
  const value = Math.round(diff / entry.ms) || (diff < 0 ? -1 : 1);

  // Prefer Intl when present
  if (typeof Intl !== 'undefined' && 'RelativeTimeFormat' in Intl) {
    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto', style: opts.style ?? 'short' });
    return rtf.format(value, entry.unit);
  }

  // Fallback: compact manual string
  const style = opts.style ?? 'short';
  const u = style === 'long' ? entry.long : (style === 'narrow' ? entry.narrow : entry.short);
  const n = Math.abs(value);
  const core = style === 'long' ? `${n} ${plural(n, u)}` : `${n}${style === 'narrow' ? '' : ''}${style === 'short' ? u : u}`;
  return value < 0 ? `${core} ago` : `in ${core}`;
}

/**
 * Convenience alias: timeAgo(date) -> "3m ago" / "in 2h"
 */
export const timeAgo = relativeTime;

/* ---------------------------- Duration Formatting ------------------------- */

/**
 * Format a duration in milliseconds into a concise string.
 * Examples:
 *  - formatDuration(95_000) -> "1m 35s"
 *  - formatDuration(25_000, { smallestUnit: 'second', style: 'long' }) -> "25 seconds"
 *  - formatDuration(36_000_000, { maxUnits: 3 }) -> "10h"
 */
export function formatDuration(msInput: number, opts: DurationOpts = {}): string {
  const style = opts.style ?? 'short';
  const maxUnits = Math.max(1, opts.maxUnits ?? 2);
  const smallest = opts.smallestUnit ?? 'second';
  const round = opts.round ?? 'nearest';
  const spacer = opts.spacer ?? '';

  const absMs = Math.abs(msInput);
  const sign = msInput < 0 ? '-' : '';

  const units = [
    { key: 'day',    ms: DAY,   labels: { short: 'd',  narrow: 'd', long: 'day' } },
    { key: 'hour',   ms: HOUR,  labels: { short: 'h',  narrow: 'h', long: 'hour' } },
    { key: 'minute', ms: MIN,   labels: { short: 'm',  narrow: 'm', long: 'minute' } },
    { key: 'second', ms: SEC,   labels: { short: 's',  narrow: 's', long: 'second' } },
    { key: 'millisecond', ms: 1,labels: { short: 'ms', narrow: 'ms',long: 'millisecond' } },
  ] as const;

  // Determine cutoff index for smallest unit
  const smallestIdx = units.findIndex(u => u.key === smallest);
  const selected: string[] = [];
  let remaining = absMs;

  for (let i = 0; i < units.length; i++) {
    const u = units[i];
    const isSmallest = i >= smallestIdx;
    if (i < smallestIdx && remaining < u.ms) continue;

    let value: number;
    if (isSmallest) {
      const raw = remaining / u.ms;
      value =
        round === 'floor' ? Math.floor(raw) :
        round === 'ceil'  ? Math.ceil(raw)  :
                            Math.round(raw);
      remaining = 0;
    } else {
      value = Math.floor(remaining / u.ms);
      remaining -= value * u.ms;
    }

    if (value > 0 || (selected.length === 0 && i === units.length - 1)) {
      const label = style === 'long'
        ? plural(value, u.labels.long)
        : (style === 'narrow' ? u.labels.narrow : u.labels.short);
      selected.push(style === 'long' ? `${value} ${label}` : `${value}${spacer}${label}`);
    }

    if (selected.length >= maxUnits || remaining <= 0) break;
  }

  return sign + (style === 'long' ? selected.join(', ') : selected.join(' '));
}

/* ---------------------------------- ETA ----------------------------------- */

/**
 * Given a start time and current progress (0..1), estimate remaining time.
 * Returns structured ETA and a friendly string (e.g., "in 12m" / "12m left").
 */
export function etaFromProgress(
  start: Dateish,
  progress: number,
  opts: { now?: Dateish; style?: RelativeStyle; suffix?: 'left' | 'eta' | 'none' } = {}
): { remainingMs: number; eta?: Date; text: string } {
  const now = getNowMs(opts.now);
  const startMs = toMillis(start);
  const elapsed = Math.max(0, now - startMs);

  if (!(progress > 0) || progress >= 1) {
    const text = progress >= 1 ? 'done' : 'estimating…';
    return { remainingMs: 0, eta: progress >= 1 ? new Date(now) : undefined, text };
  }

  const totalEstimate = elapsed / progress;        // ms per unit * total units
  const remainingMs = Math.max(0, totalEstimate - elapsed);
  const eta = new Date(now + remainingMs);
  const style = opts.style ?? 'short';

  const suffix = opts.suffix ?? 'left';
  const rel = relativeTime(eta, { now, style });
  const pretty = suffix === 'left'
    ? formatDuration(remainingMs, { style })
    : (suffix === 'eta' ? rel : formatDuration(remainingMs, { style }));

  const text = suffix === 'left' ? `${pretty} left` : (suffix === 'eta' ? rel : pretty);
  return { remainingMs, eta, text };
}

/**
 * Compute ETA from remaining work units and a processing rate (units/sec).
 * If rate <= 0 or remaining <= 0, returns an "estimating…" or "done" string.
 */
export function etaFromRate(
  remainingUnits: number,
  unitsPerSecond: number,
  opts: { now?: Dateish; style?: RelativeStyle; suffix?: 'left' | 'eta' | 'none' } = {}
): { remainingMs: number; eta?: Date; text: string } {
  const now = getNowMs(opts.now);
  if (remainingUnits <= 0) return { remainingMs: 0, eta: new Date(now), text: 'done' };
  if (!(unitsPerSecond > 0)) return { remainingMs: 0, eta: undefined, text: 'estimating…' };

  const remainingMs = (remainingUnits / unitsPerSecond) * 1000;
  const eta = new Date(now + remainingMs);
  const style = opts.style ?? 'short';
  const suffix = opts.suffix ?? 'left';
  const rel = relativeTime(eta, { now, style });
  const pretty = suffix === 'left'
    ? `${formatDuration(remainingMs, { style })} left`
    : (suffix === 'eta' ? rel : formatDuration(remainingMs, { style }));
  return { remainingMs, eta, text: pretty };
}

/**
 * Countdown helper to a fixed target time.
 * Returns hh:mm:ss (or d:hh:mm:ss) and 'done' when reached.
 */
export function countdown(
  target: Dateish,
  opts: { now?: Dateish; showDays?: boolean } = {}
): { remainingMs: number; done: boolean; formatted: string } {
  const now = getNowMs(opts.now);
  const t = toMillis(target);
  const remainingMs = Math.max(0, t - now);
  const done = remainingMs === 0;
  return { remainingMs, done, formatted: formatHMS(remainingMs, { showDays: opts.showDays }) };
}

function pad2(n: number): string { return n.toString().padStart(2, '0'); }

function formatHMS(ms: number, { showDays = true }: { showDays?: boolean } = {}): string {
  let rem = Math.floor(ms / 1000);
  const days = Math.floor(rem / 86400); rem -= days * 86400;
  const hours = Math.floor(rem / 3600); rem -= hours * 3600;
  const mins = Math.floor(rem / 60); rem -= mins * 60;
  const secs = rem;

  if (showDays && days > 0) return `${days}:${pad2(hours)}:${pad2(mins)}:${pad2(secs)}`;
  const hh = showDays ? hours + days * 24 : hours + days * 24; // flatten days into hours anyway
  return `${pad2(hh)}:${pad2(mins)}:${pad2(secs)}`;
}

/* --------------------------------- Exports -------------------------------- */

export default {
  relativeTime,
  timeAgo,
  formatDuration,
  etaFromProgress,
  etaFromRate,
  countdown,
};
