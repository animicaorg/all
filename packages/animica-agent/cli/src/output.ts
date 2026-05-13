/**
 * Tiny output helpers. ANSI colors are gated on TTY + NO_COLOR env.
 */
const useColor = !!process.stdout.isTTY && process.env.NO_COLOR === undefined;

function paint(code: number, s: string): string {
  return useColor ? `\x1b[${code}m${s}\x1b[0m` : s;
}

export const c = {
  bold: (s: string) => paint(1, s),
  dim: (s: string) => paint(2, s),
  red: (s: string) => paint(31, s),
  green: (s: string) => paint(32, s),
  yellow: (s: string) => paint(33, s),
  blue: (s: string) => paint(34, s),
  cyan: (s: string) => paint(36, s),
};

export function header(title: string): void {
  process.stdout.write(`\n${c.bold(c.cyan(title))}\n`);
}

export function kv(rows: [string, string | number | bigint | boolean | undefined | null][], indent = "  "): void {
  const width = Math.max(...rows.map(([k]) => k.length));
  for (const [k, raw] of rows) {
    const v = raw === undefined || raw === null ? c.dim("—") : String(raw);
    process.stdout.write(`${indent}${k.padEnd(width)}  ${v}\n`);
  }
}

export function table(headers: string[], rows: (string | number | bigint)[][]): void {
  if (rows.length === 0) {
    process.stdout.write(`${c.dim("(no rows)")}\n`);
    return;
  }
  const cols = headers.map((h, i) => Math.max(h.length, ...rows.map((r) => String(r[i] ?? "").length)));
  process.stdout.write(headers.map((h, i) => c.bold(h.padEnd(cols[i]))).join("  ") + "\n");
  process.stdout.write(cols.map((w) => "-".repeat(w)).join("  ") + "\n");
  for (const r of rows) {
    process.stdout.write(r.map((cell, i) => String(cell ?? "").padEnd(cols[i])).join("  ") + "\n");
  }
}

export function bullet(items: string[]): void {
  for (const i of items) process.stdout.write(`  • ${i}\n`);
}

export function warn(msg: string): void {
  process.stderr.write(`${c.yellow("warning")}: ${msg}\n`);
}

export function fail(msg: string): void {
  process.stderr.write(`${c.red("error")}: ${msg}\n`);
}

export function ok(msg: string): void {
  process.stdout.write(`${c.green("ok")}: ${msg}\n`);
}

export function info(msg: string): void {
  process.stdout.write(`${msg}\n`);
}
