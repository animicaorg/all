/**
 * Tiny argv parser. We intentionally avoid yargs/commander to keep the
 * install footprint small and the surface area auditable.
 *
 * Supports:
 *   - subcommand   positional
 *   - --flag       boolean
 *   - --key=value  / --key value
 *   - --no-flag    explicit false for booleans
 *   - --           halts parsing; remaining argv goes into `_`
 */

export interface ParsedArgs {
  command: string[];
  options: Record<string, string | boolean>;
  positionals: string[];
  remainder: string[];
}

export function parseArgs(argv: string[]): ParsedArgs {
  const command: string[] = [];
  const options: Record<string, string | boolean> = {};
  const positionals: string[] = [];
  const remainder: string[] = [];
  let i = 0;
  // Eat leading subcommand tokens (non-dash, non-positional) until we see a flag or a clearly-positional token.
  // We treat the first 1-2 leading non-dash tokens as the command path.
  while (i < argv.length && command.length < 2 && !argv[i].startsWith("-")) {
    // Heuristic: don't consume things that look like file paths, free-form text, or quoted strings.
    const tok = argv[i];
    if (tok.includes("/") || tok.includes(" ") || tok.startsWith('"') || tok.startsWith("'")) break;
    command.push(tok);
    i++;
  }
  for (; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--") {
      for (i++; i < argv.length; i++) remainder.push(argv[i]);
      break;
    }
    if (a.startsWith("--")) {
      const eq = a.indexOf("=");
      if (eq !== -1) {
        const k = a.slice(2, eq);
        const v = a.slice(eq + 1);
        options[k] = v;
        continue;
      }
      const key = a.slice(2);
      if (key.startsWith("no-")) {
        options[key.slice(3)] = false;
        continue;
      }
      const next = argv[i + 1];
      if (next === undefined || next.startsWith("-")) {
        options[key] = true;
      } else {
        options[key] = next;
        i++;
      }
      continue;
    }
    if (a.startsWith("-") && a.length > 1) {
      // short-flags treated as boolean for simplicity
      for (const c of a.slice(1)) options[c] = true;
      continue;
    }
    positionals.push(a);
  }
  return { command, options, positionals, remainder };
}

export function boolFlag(opts: Record<string, string | boolean>, name: string, def = false): boolean {
  const v = opts[name];
  if (v === undefined) return def;
  if (v === true || v === false) return v;
  if (typeof v === "string") {
    const s = v.trim().toLowerCase();
    return s === "true" || s === "1" || s === "yes" || s === "on";
  }
  return def;
}

export function stringFlag(opts: Record<string, string | boolean>, name: string, def?: string): string | undefined {
  const v = opts[name];
  if (typeof v === "string") return v;
  if (v === true) return "";
  return def;
}
