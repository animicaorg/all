import * as React from "react";
import { useAICF } from "../../hooks/useAICF";
import { shortenHex } from "../../utils/format";

type SubmitState = "idle" | "submitting";

/**
 * AIJobsPage
 * Enqueue AI jobs (model + prompt) and watch results update live.
 * Relies on hooks/useAICF for the underlying queue/poll logic.
 */
export default function AIJobsPage() {
  const { jobs, enqueueAI, refreshAll, downloadJSON, removeJob, busy } = useAICF();
  const [model, setModel] = React.useState<string>("animica/gpt-mini");
  const [prompt, setPrompt] = React.useState<string>("");
  const [maxUnits, setMaxUnits] = React.useState<number>(256);
  const [state, setState] = React.useState<SubmitState>("idle");
  const [error, setError] = React.useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!prompt.trim()) {
      setError("Prompt cannot be empty.");
      return;
    }
    try {
      setState("submitting");
      await enqueueAI({ model, prompt, maxUnits });
      setState("idle");
    } catch (err: any) {
      setState("idle");
      setError(err?.message ?? String(err));
    }
  }

  function fillDemo() {
    setModel("animica/gpt-mini");
    setPrompt("Write a short haiku about deterministic blockchains.");
    setMaxUnits(128);
  }

  return (
    <div className="p-4 space-y-6">
      <header>
        <h1 className="text-lg font-semibold">AI Jobs</h1>
        <p className="text-sm text-[color:var(--muted,#6b7280)]">
          Enqueue a small AI job via AICF. Results are proven on-chain next block and exposed as verifiable
          outputs for contracts. This page lets you test the off-chain flow from the IDE.
        </p>
      </header>

      <section className="rounded border bg-white">
        <form onSubmit={onSubmit} className="p-4 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <Field label="Model">
              <select
                className="w-full border rounded px-3 py-2 text-sm bg-white"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              >
                <option value="animica/gpt-mini">animica/gpt-mini (demo)</option>
                <option value="animica/gpt-medium">animica/gpt-medium</option>
                <option value="animica/llama-lite">animica/llama-lite</option>
              </select>
            </Field>
            <Field label="Max AI Units">
              <input
                type="number"
                min={32}
                step={32}
                className="w-full border rounded px-3 py-2 text-sm"
                value={maxUnits}
                onChange={(e) => setMaxUnits(Number(e.target.value || 0))}
              />
            </Field>
            <div className="flex items-end gap-2">
              <button
                type="button"
                className="px-3 py-2 border rounded text-sm"
                onClick={fillDemo}
                aria-label="Fill with demo values"
              >
                Fill demo
              </button>
              <button
                type="button"
                className="px-3 py-2 border rounded text-sm"
                onClick={() => refreshAll()}
                disabled={busy}
                title="Refresh all jobs"
              >
                Refresh
              </button>
            </div>
          </div>

          <Field label="Prompt">
            <textarea
              className="w-full border rounded px-3 py-2 text-sm min-h-[120px]"
              placeholder="Describe your prompt…"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
          </Field>

          {error && (
            <div role="alert" className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">
              {error}
            </div>
          )}

          <div className="flex items-center gap-2">
            <button
              type="submit"
              className="px-4 py-2 rounded bg-blue-600 text-white text-sm disabled:opacity-60"
              disabled={state === "submitting"}
            >
              {state === "submitting" ? "Enqueuing…" : "Enqueue Job"}
            </button>
            <span className="text-xs text-[color:var(--muted,#6b7280)]">
              Jobs execute off-chain; results are referenced by proofs on-chain.
            </span>
          </div>
        </form>
      </section>

      <JobsTable
        jobs={jobs}
        onDownload={(id) => downloadJSON(id)}
        onRemove={(id) => removeJob(id)}
      />
    </div>
  );
}

/* --------------------------------- UI bits -------------------------------- */

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-1">{label}</div>
      {children}
    </label>
  );
}

type JobRow = {
  id: string;
  kind: "AI";
  status: "queued" | "assigned" | "running" | "completed" | "failed";
  model: string;
  prompt: string;
  createdAt?: string;
  completedAt?: string;
  taskId?: string; // deterministic id if known
  result?: { digest: string; preview?: string; size?: number } | null;
  error?: string | null;
};

function JobsTable({
  jobs,
  onDownload,
  onRemove,
}: {
  jobs: JobRow[];
  onDownload: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <section className="rounded border bg-white overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead className="bg-[color:var(--table-head,#f9fafb)] text-left">
          <tr>
            <Th>Job</Th>
            <Th>Model</Th>
            <Th>Status</Th>
            <Th>Task ID</Th>
            <Th>Result</Th>
            <Th>When</Th>
            <Th className="text-right pr-3">Actions</Th>
          </tr>
        </thead>
        <tbody>
          {jobs.length === 0 ? (
            <tr>
              <td colSpan={7} className="p-4 text-center text-[color:var(--muted,#6b7280)]">
                No jobs yet. Enqueue one above.
              </td>
            </tr>
          ) : (
            jobs.map((j) => <JobTR key={j.id} job={j} onDownload={onDownload} onRemove={onRemove} />)
          )}
        </tbody>
      </table>
    </section>
  );
}

function Th({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <th className={`px-3 py-2 text-xs font-medium uppercase tracking-wide ${className}`}>{children}</th>;
}

function TD({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-3 py-2 align-top ${className}`}>{children}</td>;
}

function JobTR({
  job,
  onDownload,
  onRemove,
}: {
  job: JobRow;
  onDownload: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  const statusBadge = badgeFor(job.status);
  return (
    <tr className="border-t">
      <TD>
        <div className="font-mono text-xs">{shorten(job.id)}</div>
        <div className="text-[11px] text-[color:var(--muted,#6b7280)] line-clamp-1">{job.prompt}</div>
      </TD>
      <TD>{job.model}</TD>
      <TD>
        <span className={`px-2 py-1 rounded text-[11px] text-white ${statusBadge.className}`}>{statusBadge.label}</span>
        {job.error && <div className="text-xs text-red-700 mt-1 break-all">{job.error}</div>}
      </TD>
      <TD className="font-mono text-xs">{job.taskId ? shorten(job.taskId) : "—"}</TD>
      <TD>
        {job.result ? (
          <div className="space-y-1">
            <div className="text-xs">
              digest: <span className="font-mono">{shorten(job.result.digest)}</span>
              {typeof job.result.size === "number" ? <span className="ml-2 text-[color:var(--muted,#6b7280)]">{job.result.size}B</span> : null}
            </div>
            {job.result.preview && <pre className="text-xs bg-[color:var(--muted-bg,#f3f4f6)] rounded p-2 max-w-[420px] whitespace-pre-wrap break-words">{job.result.preview}</pre>}
          </div>
        ) : (
          <span className="text-[color:var(--muted,#6b7280)]">—</span>
        )}
      </TD>
      <TD>
        <div className="text-xs">{job.createdAt ? formatDate(job.createdAt) : "—"}</div>
        <div className="text-[11px] text-[color:var(--muted,#6b7280)]">
          {job.completedAt ? `done ${timeAgo(job.completedAt)}` : ""}
        </div>
      </TD>
      <TD className="text-right">
        <div className="flex items-center gap-2 justify-end">
          <button
            type="button"
            className="px-2 py-1 border rounded text-xs disabled:opacity-60"
            onClick={() => onDownload(job.id)}
            disabled={!job.result}
            title="Download result JSON"
          >
            Download
          </button>
          <button
            type="button"
            className="px-2 py-1 border rounded text-xs"
            onClick={() => onRemove(job.id)}
            title="Remove from list"
          >
            Remove
          </button>
        </div>
      </TD>
    </tr>
  );
}

/* --------------------------------- utils --------------------------------- */

function badgeFor(
  status: JobRow["status"]
): { label: string; className: string } {
  switch (status) {
    case "queued":
      return { label: "Queued", className: "bg-gray-500" };
    case "assigned":
      return { label: "Assigned", className: "bg-blue-600" };
    case "running":
      return { label: "Running", className: "bg-indigo-600" };
    case "completed":
      return { label: "Completed", className: "bg-green-600" };
    case "failed":
      return { label: "Failed", className: "bg-red-600" };
  }
}

function shorten(v?: string, head = 8, tail = 6) {
  if (!v) return "—";
  if (v.length <= head + tail + 3) return v;
  return `${v.slice(0, head)}…${v.slice(-tail)}`;
}

function formatDate(iso: string) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function timeAgo(iso: string) {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const s = Math.max(1, Math.floor((now - then) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

// re-exported small helper until UI fully switches to shared formatter
export const _shortenHex = shortenHex;
