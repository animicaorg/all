import * as React from "react";
import * as aicf from "../../services/aicf";

/**
 * QuantumJobsPage — simple panel to enqueue quantum trap-circuit jobs and
 * inspect their status/results. Uses studio-web/services/aicf.ts helpers.
 *
 * The job spec here mirrors the high-level fields described in the repo plan:
 * - circuit: JSON payload (gate list, or provider-native IR)
 * - shots:   number of executions
 * - traps:   number of trap circuits to mix for verification
 * - depth:   circuit depth (used for unit estimation)
 * - width:   number of qubits (used for unit estimation)
 * - maxUnits: user-specified spending cap for scheduling/pricing
 */

type QuantumJob = {
  id: string;
  kind: "quantum";
  status: "queued" | "assigned" | "running" | "completed" | "failed" | "expired";
  createdAt?: string;
  updatedAt?: string;
  // Optional result preview fields (provider-specific)
  resultSummary?: string;
};

type SubmitState =
  | { state: "idle" }
  | { state: "submitting" }
  | { state: "success"; jobId: string }
  | { state: "error"; message: string };

export default function QuantumJobsPage() {
  const [circuit, setCircuit] = React.useState<string>(defaultCircuitJson);
  const [shots, setShots] = React.useState<number>(256);
  const [traps, setTraps] = React.useState<number>(16);
  const [depth, setDepth] = React.useState<number>(8);
  const [width, setWidth] = React.useState<number>(4);
  const [units, setUnits] = React.useState<number>(estimateUnits(8, 4, 256, 16));
  const [err, setErr] = React.useState<string | null>(null);
  const [submit, setSubmit] = React.useState<SubmitState>({ state: "idle" });

  const [jobs, setJobs] = React.useState<QuantumJob[]>([]);
  const [loadingJobs, setLoadingJobs] = React.useState<boolean>(false);

  React.useEffect(() => {
    setUnits(estimateUnits(depth, width, shots, traps));
  }, [depth, width, shots, traps]);

  React.useEffect(() => {
    refreshJobs().catch(() => void 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refreshJobs() {
    setLoadingJobs(true);
    try {
      // Best-effort: list latest quantum jobs (if the service exposes it).
      if (typeof (aicf as any).listJobs === "function") {
        const res = await (aicf as any).listJobs({ kind: "quantum", limit: 20 });
        // Normalize into our local view
        const list: QuantumJob[] = Array.isArray(res)
          ? res.map(normalizeJob)
          : Array.isArray(res?.items)
          ? res.items.map(normalizeJob)
          : [];
        setJobs(list);
      }
    } finally {
      setLoadingJobs(false);
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setSubmit({ state: "submitting" });

    let circuitObj: any;
    try {
      circuitObj = JSON.parse(circuit);
    } catch (e: any) {
      setSubmit({ state: "error", message: "Circuit JSON is invalid." });
      setErr("Circuit JSON is invalid.");
      return;
    }

    const spec = {
      circuit: circuitObj,
      shots,
      traps,
      depth,
      width,
      maxUnits: units,
    };

    try {
      // Prefer enqueueQuantum if available; otherwise fall back to a generic enqueue
      let jobId: string | undefined;
      if (typeof (aicf as any).enqueueQuantum === "function") {
        jobId = await (aicf as any).enqueueQuantum(spec);
      } else if (typeof (aicf as any).enqueueJob === "function") {
        jobId = await (aicf as any).enqueueJob("quantum", spec);
      } else {
        throw new Error("AICF service does not expose enqueueQuantum()");
      }

      setSubmit({ state: "success", jobId: String(jobId) });
      await refreshJobs();

      // Fire-and-forget polling if available (no UI lock)
      if (typeof (aicf as any).pollJobUntil === "function" && jobId) {
        (aicf as any)
          .pollJobUntil(jobId, { timeoutMs: 60_000, intervalMs: 1500 })
          .then(refreshJobs)
          .catch(() => void 0);
      }
    } catch (e: any) {
      const message = e?.message ?? String(e);
      setSubmit({ state: "error", message });
      setErr(message);
    }
  }

  const disableForm = submit.state === "submitting";

  return (
    <div className="p-4 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Quantum Jobs</h1>
          <p className="text-sm text-[color:var(--muted,#6b7280)]">
            Enqueue a quantum trap-circuit job. Results are referenced by on-chain proofs.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="px-3 py-2 border rounded text-sm"
            onClick={() => {
              setCircuit(defaultCircuitJson);
              setShots(256);
              setTraps(16);
              setDepth(8);
              setWidth(4);
            }}
            disabled={disableForm}
          >
            Reset
          </button>
          <button
            className="px-3 py-2 border rounded text-sm"
            onClick={() => {
              setCircuit(exampleCircuit("ghz", 4, 8));
              setShots(512);
              setTraps(24);
              setDepth(10);
              setWidth(5);
            }}
            disabled={disableForm}
          >
            Load demo
          </button>
        </div>
      </header>

      <form onSubmit={onSubmit} className="space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <Field label="Shots">
            <input
              type="number"
              min={32}
              step={32}
              className="w-full border rounded px-3 py-2 text-sm"
              value={shots}
              onChange={(e) => setShots(snap32(e.target.value))}
              disabled={disableForm}
            />
          </Field>

          <Field label="Traps">
            <input
              type="number"
              min={4}
              step={4}
              className="w-full border rounded px-3 py-2 text-sm"
              value={traps}
              onChange={(e) => setTraps(snap4(e.target.value))}
              disabled={disableForm}
            />
          </Field>

          <Field label="Depth">
            <input
              type="number"
              min={1}
              step={1}
              className="w-full border rounded px-3 py-2 text-sm"
              value={depth}
              onChange={(e) => setDepth(Math.max(1, Number(e.target.value || 1)))}
              disabled={disableForm}
            />
          </Field>

          <Field label="Width (qubits)">
            <input
              type="number"
              min={1}
              step={1}
              className="w-full border rounded px-3 py-2 text-sm"
              value={width}
              onChange={(e) => setWidth(Math.max(1, Number(e.target.value || 1)))}
              disabled={disableForm}
            />
          </Field>
        </div>

        <Field label="Circuit JSON">
          <textarea
            className="w-full border rounded px-3 py-2 text-sm min-h-[200px] font-mono"
            value={circuit}
            onChange={(e) => setCircuit(e.target.value)}
            disabled={disableForm}
            spellCheck={false}
          />
          <div className="text-xs text-[color:var(--muted,#6b7280)] mt-1">
            Expected shape is provider-specific. Keep to pure data (no functions).
          </div>
        </Field>

        <div className="flex items-center justify-between">
          <div className="text-sm">
            <span className="text-[color:var(--muted,#6b7280)]">Estimated units:</span>{" "}
            <span className="font-medium">{units}</span>
          </div>
          <button
            type="submit"
            className="px-4 py-2 rounded bg-blue-600 text-white text-sm disabled:opacity-60"
            disabled={disableForm}
          >
            {submit.state === "submitting" ? "Enqueuing…" : "Enqueue Quantum Job"}
          </button>
        </div>

        {err && (
          <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">
            {err}
          </div>
        )}
        {submit.state === "success" && (
          <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded p-2">
            Job submitted: <code className="font-mono">{submit.jobId}</code>
          </div>
        )}
      </form>

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">Recent quantum jobs</h2>
          <button
            className="px-3 py-2 border rounded text-sm"
            onClick={() => refreshJobs()}
            disabled={loadingJobs}
          >
            {loadingJobs ? "Refreshing…" : "Refresh"}
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b">
                <Th>ID</Th>
                <Th>Status</Th>
                <Th>Updated</Th>
                <Th>Result</Th>
              </tr>
            </thead>
            <tbody>
              {jobs.length === 0 ? (
                <tr>
                  <td colSpan={4} className="py-6 text-center text-[color:var(--muted,#6b7280)]">
                    No jobs yet. Submit one above.
                  </td>
                </tr>
              ) : (
                jobs.map((j) => (
                  <tr key={j.id} className="border-b last:border-0">
                    <Td>
                      <code className="font-mono text-xs break-all">{j.id}</code>
                    </Td>
                    <Td>
                      <StatusBadge status={j.status} />
                    </Td>
                    <Td>{formatWhen(j.updatedAt ?? j.createdAt)}</Td>
                    <Td>
                      <span className="text-[color:var(--muted,#6b7280)]">
                        {j.resultSummary ?? "—"}
                      </span>
                    </Td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

/* ------------------------------- UI helpers ------------------------------- */

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-1">
        {label}
      </div>
      {children}
    </label>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="py-2 pr-3 text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">{children}</th>;
}
function Td({ children }: { children: React.ReactNode }) {
  return <td className="py-2 pr-3">{children}</td>;
}

function StatusBadge({ status }: { status: QuantumJob["status"] }) {
  const cls =
    status === "completed"
      ? "bg-green-50 text-green-700 border-green-200"
      : status === "failed"
      ? "bg-red-50 text-red-700 border-red-200"
      : status === "running" || status === "assigned"
      ? "bg-blue-50 text-blue-700 border-blue-200"
      : status === "expired"
      ? "bg-yellow-50 text-yellow-700 border-yellow-200"
      : "bg-gray-50 text-gray-700 border-gray-200";
  return <span className={`px-2 py-1 rounded border text-xs ${cls}`}>{status}</span>;
}

/* --------------------------------- logic ---------------------------------- */

function normalizeJob(x: any): QuantumJob {
  const status: QuantumJob["status"] =
    x?.status && typeof x.status === "string" ? x.status : "queued";
  const summary =
    x?.result?.summary ??
    x?.resultSummary ??
    (x?.status === "completed" && x?.result
      ? summarizeResult(x.result)
      : undefined);
  return {
    id: String(x?.id ?? x?.jobId ?? x?.task_id ?? "unknown"),
    kind: "quantum",
    status,
    createdAt: x?.createdAt ?? x?.created_at ?? x?.ts_created,
    updatedAt: x?.updatedAt ?? x?.updated_at ?? x?.ts_updated,
    resultSummary: summary,
  };
}

function summarizeResult(result: any): string {
  try {
    if (!result) return "ok";
    if (typeof result === "string") return result.slice(0, 120);
    if (Array.isArray(result?.counts)) {
      // Common QPU format: histogram counts
      const top = [...result.counts]
        .sort((a: any, b: any) => (b?.count ?? 0) - (a?.count ?? 0))
        .slice(0, 3)
        .map((r: any) => `${r.bits}:${r.count}`)
        .join(", ");
      return `counts: ${top}`;
    }
    if (result?.histogram && typeof result.histogram === "object") {
      const entries = Object.entries(result.histogram as Record<string, number>)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(([k, v]) => `${k}:${v}`)
        .join(", ");
      return `hist: ${entries}`;
    }
    const s = JSON.stringify(result);
    return s.length > 120 ? s.slice(0, 117) + "…" : s;
  } catch {
    return "ok";
  }
}

function formatWhen(ts?: string): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

/** Rough units estimator for pricing previews. Tuned for UX only. */
function estimateUnits(depth: number, width: number, shots: number, traps: number): number {
  const base = Math.max(1, depth) * Math.max(1, width) * Math.max(1, Math.floor(shots / 32));
  const trapOverhead = 1 + Math.min(2, traps / 32);
  const units = Math.round(base * trapOverhead);
  // Clamp to reasonable range for UI
  return Math.max(32, Math.min(100_000, units));
}

function snap32(v: string): number {
  const n = Math.max(32, Number(v || 0));
  return Math.round(n / 32) * 32;
}
function snap4(v: string): number {
  const n = Math.max(4, Number(v || 0));
  return Math.round(n / 4) * 4;
}

/* ------------------------------- demo data -------------------------------- */

const defaultCircuitJson = JSON.stringify(
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
    // traps vector is provider-defined; include a hint field only
    traps_hint: { ratio: 0.1 },
  },
  null,
  2
);

function exampleCircuit(kind: "ghz" | "qft", width: number, depth: number): string {
  if (kind === "ghz") {
    const gates: any[] = [{ op: "h", q: 0 }];
    for (let i = 1; i < width; i++) gates.push({ op: "cx", control: 0, target: i });
    for (let i = 0; i < width; i++) gates.push({ op: "measure", q: i });
    return JSON.stringify({ name: "ghz", width, depth, gates, traps_hint: { ratio: 0.12 } }, null, 2);
  }
  // simple toy QFT-ish sequence (non-physical; demo only)
  const gates: any[] = [];
  for (let i = 0; i < width; i++) {
    gates.push({ op: "h", q: i });
    for (let j = i + 1; j < width; j++) {
      gates.push({ op: "cp", control: j, target: i, theta: Math.PI / 2 ** (j - i) });
    }
  }
  for (let i = 0; i < width / 2; i++) gates.push({ op: "swap", a: i, b: width - 1 - i });
  for (let i = 0; i < width; i++) gates.push({ op: "measure", q: i });
  return JSON.stringify({ name: "qft", width, depth, gates, traps_hint: { ratio: 0.15 } }, null, 2);
}
