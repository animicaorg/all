/**
 * classnames.ts — tiny, dependency-free class name combiner
 *
 * Accepts strings, numbers, arrays, and object maps `{class: boolean}`.
 * Falsy values (false | null | undefined | 0-length strings) are ignored.
 *
 * Examples:
 *   cn('btn', isPrimary && 'btn--primary', ['p-2', { 'opacity-50': disabled }])
 *   // -> "btn btn--primary p-2"
 *
 * This intentionally does not attempt Tailwind "last rule wins" merging.
 * If you need that behavior, layer another utility at call-sites that
 * applies your own merge rules.
 */

export type ClassInput =
  | string
  | number
  | null
  | undefined
  | false
  | ClassInput[]
  | { [className: string]: unknown };

/** Internal: recursively flatten inputs into a single array of class strings. */
function collect(input: ClassInput, out: string[]) {
  if (!input) return;

  const t = typeof input;

  if (t === 'string' || t === 'number') {
    const s = String(input).trim();
    if (s) out.push(s);
    return;
  }

  if (Array.isArray(input)) {
    for (const v of input) collect(v as ClassInput, out);
    return;
  }

  if (t === 'object') {
    for (const [key, val] of Object.entries(input as Record<string, unknown>)) {
      // Treat truthy values (not false/0/""/null/undefined) as enabled classes.
      if (val) {
        const k = key.trim();
        if (k) out.push(k);
      }
    }
  }
}

/**
 * Combine class names into a single space-separated string.
 * Ignores falsy entries, preserves order, does not deduplicate.
 */
export function cn(...inputs: ClassInput[]): string {
  const out: string[] = [];
  for (const i of inputs) collect(i, out);
  return out.join(' ');
}

/** Alias of `cn` for folks who prefer the "clsx/cx" naming. */
export const clsx = cn;
/** Alias of `cn`. */
export const cx = cn;

/**
 * Convenience helper: include `thenCls` if condition is truthy, otherwise
 * include optional `elseCls`.
 */
export function classIf(
  condition: unknown,
  thenCls: ClassInput,
  elseCls?: ClassInput,
): string {
  return cn(condition ? thenCls : null, !condition && elseCls ? elseCls : null);
}

/**
 * Merge a base class list with overrides. This is a light wrapper around `cn`
 * provided for semantic clarity at call-sites.
 */
export function merge(base: ClassInput, ...overrides: ClassInput[]): string {
  return cn(base, ...overrides);
}

export default cn;
