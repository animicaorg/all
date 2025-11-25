import * as React from "react";

/**
 * QuantumJobForm — focused JSON circuit editor with validation & helpers.
 *
 * This component is intentionally self-contained so it can be reused in
 * different pages/dialogs. It accepts either a string (raw JSON) or an
 * object as its `value`, and emits both the string and parsed form in
 * `onChange`. It performs lightweight validation and exposes error state.
 */

export type QuantumCircuit = {
  name?: string;
  width?: number;
  depth?: number;
  gates?: any[];
  [key: string]: any;
};

export type ChangeInfo = { valid: boolean; error?: string; stats?: CircuitStats };

export interface QuantumJobFormProps {
  /** The current circuit value; can be a JSON string or object. */
  value?: string | QuantumCircuit;
  /** Called whenever the text changes; includes parsed JSON (or null) and validation info. */
  onChange?: (nextText: string, parsed: QuantumCircuit | null, info: ChangeInfo) => void;
  /** Disable inputs (e.g., while submitting). */
  disabled?: boolean;
  /** Minimum editor height in pixels. */
  minHeight?: number;
  /** Show sample circuits dropdown. */
  allowSamples?: boolean;
  /** Optional className applied to root container. */
  className?: string;
  /** Optional label to display above the editor (defaults to "Circuit JSON"). */
  label?: string;
}

type CircuitStats = {
  width?: number;
  depth?: number;
  gateCount?: number;
};

/* -------------------------------- Component -------------------------------- */

export default function QuantumJobForm({
  value,
  onChange,
  disabled,
  minHeight = 220,
  allowSamples = true,
  className,
  label = "Circuit JSON",
}: QuantumJobFormProps) {
  const initialText = React.useMemo(() => {
    if (typeof value === "string") return value;
    if (value && typeof value === "object") return safeStringify(value, 2);
    return defaultCircuitJson;
  }, [value]);

  const [text, setText] = React.useState<string>(initialText);
  const [error, setError] = React.useState<string | undefined>();
  const [stats, setStats] = React.useState<CircuitStats | undefined>();

  // Keep internal state in sync if parent `value` changes
  React.useEffect(() => {
    setText(initialText);
    const { parsed, info } = tryParseAndValidate(initialText);
    setError(info.error);
    setStats(info.stats);
  }, [initialText]);

  function emit(nextText: string) {
    const { parsed, info } = tryParseAndValidate(nextText);
    setError(info.error);
    setStats(info.stats);
    onChange?.(nextText, parsed, info);
  }

  function handleInput(next: string) {
    setText(next);
    emit(next);
  }

  function onFormat() {
    const { parsed } = tryParseAndValidate(text);
    if (!parsed) return;
    const pretty = stableStringify(parsed, 2);
    setText(pretty);
    emit(pretty);
  }

  function onSample(kind: SampleKind) {
    const sample = sampleCircuit(kind);
    setText(sample);
    emit(sample);
  }

  const lines = React.useMemo(() => countLines(text), [text]);

  return (
    <div className={className}>
      <div className="flex items-center justify-between mb-1">
        <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
          {label}
        </div>
        <div className="flex items-center gap-2">
          {allowSamples && (
            <SampleMenu
              onChoose={onSample}
              disabled={!!disabled}
            />
          )}
          <button
            type="button"
            className="px-3 py-1.5 border rounded text-xs"
            onClick={onFormat}
            disabled={!!disabled}
            title="Pretty-print JSON and sort keys"
          >
            Format
          </button>
        </div>
      </div>

      <textarea
        className="w-full border rounded px-3 py-2 text-sm font-mono"
        style={{ minHeight }}
        value={text}
        onChange={(e) => handleInput(e.target.value)}
        disabled={disabled}
        spellCheck={false}
        aria-invalid={!!error}
        aria-describedby="circuit-help"
      />

      <div id="circuit-help" className="mt-1 text-xs flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-[color:var(--muted,#6b7280)]">
          {lines} {lines === 1 ? "line" : "lines"}
        </span>
        {stats && (
          <span className="text-[color:var(--muted,#6b7280)]">
            {stats.width ? `qubits:${stats.width} · ` : ""}
            {stats.depth ? `depth:${stats.depth} · ` : ""}
            {typeof stats.gateCount === "number" ? `gates:${stats.gateCount}` : ""}
          </span>
        )}
        {error ? (
          <span className="text-red-700 bg-red-50 border border-red-200 rounded px-2 py-0.5">
            {error}
          </span>
        ) : (
          <span className="text-green-700 bg-green-50 border border-green-200 rounded px-2 py-0.5">
            Valid JSON
          </span>
        )}
      </div>
    </div>
  );
}

/* --------------------------------- Samples --------------------------------- */

type SampleKind = "bell" | "ghz" | "qft";

function SampleMenu({
  onChoose,
  disabled,
}: {
  onChoose: (k: SampleKind) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  return (
    <div className="relative">
      <button
        type="button"
        className="px-3 py-1.5 border rounded text-xs"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        Load sample
      </button>
      {open && (
        <div className="absolute right-0 mt-1 w-40 bg-white border rounded shadow z-10 text-sm">
          <button
            type="button"
            className="w-full text-left px-3 py-2 hover:bg-gray-50"
            onClick={() => {
              setOpen(false);
              onChoose("bell");
            }}
          >
            Bell pair
          </button>
          <button
            type="button"
            className="w-full text-left px-3 py-2 hover:bg-gray-50"
            onClick={() => {
              setOpen(false);
              onChoose("ghz");
            }}
          >
            GHZ (4q)
          </button>
          <button
            type="button"
            className="w-full text-left px-3 py-2 hover:bg-gray-50"
            onClick={() => {
              setOpen(false);
              onChoose("qft");
            }}
          >
            QFT (5q)
          </button>
        </div>
      )}
    </div>
  );
}

function sampleCircuit(kind: SampleKind): string {
  if (kind === "bell") return defaultCircuitJson;
  if (kind === "ghz") return exampleCircuit("ghz", 4, 10);
  return exampleCircuit("qft", 5, 12);
}

/* ------------------------------- Validation ------------------------------- */

function tryParseAndValidate(input: string): { parsed: QuantumCircuit | null; info: ChangeInfo } {
  try {
    const parsed = JSON.parse(input) as QuantumCircuit;
    const vErr = validateCircuit(parsed);
    const stats = inferStats(parsed);
    if (vErr) return { parsed, info: { valid: false, error: vErr, stats } };
    return { parsed, info: { valid: true, stats } };
  } catch (e: any) {
    // Decode common JSON error messages to something helpful
    const msg = e?.message || "Invalid JSON";
    const nice = prettifyJsonError(msg);
    return { parsed: null, info: { valid: false, error: nice } };
  }
}

function validateCircuit(c: QuantumCircuit): string | undefined {
  if (!c || typeof c !== "object") return "Circuit must be a JSON object";
  if (c.width !== undefined && (!Number.isFinite(c.width) || c.width <= 0)) {
    return "width must be a positive number";
  }
  if (c.depth !== undefined && (!Number.isFinite(c.depth) || c.depth <= 0)) {
    return "depth must be a positive number";
  }
  if (c.gates !== undefined) {
    if (!Array.isArray(c.gates)) return "gates must be an array";
    for (let i = 0; i < c.gates.length; i++) {
      const g = c.gates[i];
      if (!g || typeof g !== "object") return `gate[${i}] must be an object`;
      if (typeof g.op !== "string") return `gate[${i}].op must be a string`;
    }
  }
  return undefined;
}

function inferStats(c?: QuantumCircuit | null): CircuitStats | undefined {
  if (!c || typeof c !== "object") return;
  const gateCount = Array.isArray(c.gates) ? c.gates.length : undefined;
  return {
    width: typeof c.width === "number" ? c.width : undefined,
    depth: typeof c.depth === "number" ? c.depth : undefined,
    gateCount,
  };
}

/* --------------------------------- Utils ---------------------------------- */

function countLines(s: string): number {
  if (!s) return 0;
  // Count \n; if string does not end in newline, add one line.
  let n = 0;
  for (let i = 0; i < s.length; i++) if (s.charCodeAt(i) === 10) n++;
  return s.length === 0 ? 0 : n + 1;
}

function prettifyJsonError(msg: string): string {
  // Common V8 error: "Unexpected token } in JSON at position 123"
  const m = msg.match(/at position (\d+)/i);
  if (!m) return msg;
  const pos = Number(m[1]);
  if (!Number.isFinite(pos)) return msg;
  return `Invalid JSON (pos ${pos}): ${msg.split(":")[0]}`;
}

function stableStringify(obj: any, space?: number): string {
  return JSON.stringify(sortRec(obj), null, space);
}

function safeStringify(obj: any, space?: number): string {
  try {
    return JSON.stringify(obj, null, space);
  } catch {
    return String(obj);
  }
}

function sortRec(x: any): any {
  if (Array.isArray(x)) return x.map(sortRec);
  if (x && typeof x === "object") {
    const out: any = {};
    for (const k of Object.keys(x).sort()) out[k] = sortRec(x[k]);
    return out;
  }
  return x;
}

/* ------------------------------- Demo data -------------------------------- */

export const defaultCircuitJson = JSON.stringify(
  {
    name: "bell_pair",
    width: 2,
    depth: 3,
    gates: [
      { op: "h", q: 0 },
      { op: "cx", control: 0, target: 1 },
      { op: "measure", q: 0 },
      { op: "measure", q: 1 },
    ],
    traps_hint: { ratio: 0.1 },
  },
  null,
  2
);

export function exampleCircuit(kind: "ghz" | "qft", width: number, depth: number): string {
  if (kind === "ghz") {
    const gates: any[] = [{ op: "h", q: 0 }];
    for (let i = 1; i < width; i++) gates.push({ op: "cx", control: 0, target: i });
    for (let i = 0; i < width; i++) gates.push({ op: "measure", q: i });
    return JSON.stringify({ name: "ghz", width, depth, gates, traps_hint: { ratio: 0.12 } }, null, 2);
  }
  // Toy QFT-like sequence for demo purposes
  const gates: any[] = [];
  for (let i = 0; i < width; i++) {
    gates.push({ op: "h", q: i });
    for (let j = i + 1; j < width; j++) {
      gates.push({ op: "cp", control: j, target: i, theta: Math.PI / 2 ** (j - i) });
    }
  }
  for (let i = 0; i < Math.floor(width / 2); i++) gates.push({ op: "swap", a: i, b: width - 1 - i });
  for (let i = 0; i < width; i++) gates.push({ op: "measure", q: i });
  return JSON.stringify({ name: "qft", width, depth, gates, traps_hint: { ratio: 0.15 } }, null, 2);
}
