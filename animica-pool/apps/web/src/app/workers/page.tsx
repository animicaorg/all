"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

interface Worker {
  id: string; name: string; status: string; isVerified: boolean;
  cpuModel?: string; gpuModel?: string; benchmarkScore?: string | number | null; lastSeenAt?: string | null;
}

const STATUS_COLOR: Record<string, string> = {
  online: "text-neon-green", busy: "text-neon-blue", benchmarking: "text-yellow-400",
  pending: "text-white/50", offline: "text-white/40", banned: "text-red-400", disabled: "text-red-400",
};

export default function WorkersPage() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [mode, setMode] = useState("all");
  const [token, setToken] = useState<string | null>(null);

  const { data: workers, isLoading, error } = useQuery({ queryKey: ["workers"], queryFn: () => api<Worker[]>("/api/workers"), retry: false });

  const create = useMutation({
    mutationFn: () => api<{ token: string }>("/api/workers", { method: "POST", body: { name, earningMode: mode } }),
    onSuccess: (d) => { setToken(d.token); setName(""); qc.invalidateQueries({ queryKey: ["workers"] }); },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/workers/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workers"] }),
  });

  if (error) return <p className="card">Sign in to manage workers. <Link href="/login" className="text-neon-blue">Sign in</Link></p>;

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-4">
          <span className="badge">Compute</span>
          <h1 className="text-4xl font-semibold tracking-tightest text-white md:text-5xl">
            <span className="grad-text">Workers</span>
          </h1>
          <p className="max-w-2xl text-lg text-white/65">
            Register your machines, mint tokens, and put idle compute to work.
          </p>
        </div>
        <Link href="/workers/install" className="btn-ghost text-sm">Install guide</Link>
      </header>

      <div className="card flex flex-wrap items-end gap-4">
        <label className="text-sm text-white/70">Name<input className="field mt-1.5" value={name} onChange={(e) => setName(e.target.value)} placeholder="my-gpu-box" /></label>
        <label className="text-sm text-white/70">Earning mode
          <select className="field mt-1.5" value={mode} onChange={(e) => setMode(e.target.value)}>
            {["all", "mining", "inference", "rental"].map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <button className="btn-primary" onClick={() => create.mutate()} disabled={create.isPending || !name}>Create worker</button>
      </div>

      {token && (
        <div className="card border-neon-green/40 space-y-2.5">
          <p className="text-sm text-white/70">Worker token — use it in the installer (also re-readable any time from the worker’s row below):</p>
          <code className="block break-all text-neon-green">{token}</code>
          <pre className="overflow-x-auto rounded-xl border border-white/10 bg-black/40 p-3.5 text-sm">{`curl -fsSL https://pool.animica.org/install-worker.sh | bash -s -- ${token}`}</pre>
        </div>
      )}

      {isLoading ? <p className="text-white/60">Loading…</p> : (
        <div className="space-y-2">
          {(workers ?? []).map((w) => (
            <WorkerRow key={w.id} w={w} onDelete={() => { if (confirm(`Delete worker "${w.name}"? This stops it earning and unlists any rental. The running agent/container should be stopped separately.`)) remove.mutate(w.id); }} deleting={remove.isPending} />
          ))}
          {(workers ?? []).length === 0 && <p className="text-white/60">No workers yet — create one above.</p>}
        </div>
      )}
    </div>
  );
}

interface WorkerStats {
  rental: { listed: boolean; pricePerHourUsd: number | null; listingId: string | null };
  token: string | null;
  status: string; online: boolean; earningMode: string;
  hardware: { cpuModel?: string; gpuModel?: string; ramGb?: number; vramGb?: number; os?: string; region?: string };
  benchmarkScore: number | null; lastBenchmarkAt: string | null; lastSeenAt: string | null;
  supportedModels: string[]; jobs: { completed: number; failed: number }; earningsUsd: number; tokensServed: number;
  createdAt: string;
  recentJobs: { id: string; kind: string; model?: string; status: string; outputTokens: number; rewardUsd?: string | number | null; createdAt: string }[];
}

function WorkerRow({ w, onDelete, deleting }: { w: Worker; onDelete: () => void; deleting: boolean }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [price, setPrice] = useState("0.20");
  const { data: s, isLoading } = useQuery({
    queryKey: ["worker-stats", w.id],
    queryFn: () => api<WorkerStats>(`/api/workers/${w.id}/stats`),
    enabled: open,
    refetchInterval: open ? 15000 : false,
  });
  const listMut = useMutation({
    mutationFn: () => api(`/api/workers/${w.id}/rental`, { method: "POST", body: { pricePerHourUsd: Number(price) } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["worker-stats", w.id] }),
  });
  const unlistMut = useMutation({
    mutationFn: () => api(`/api/workers/${w.id}/rental`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["worker-stats", w.id] }),
  });
  const fmtTime = (t?: string | null) => (t ? new Date(t).toLocaleString() : "—");

  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <button className="flex items-center gap-2 text-left" onClick={() => setOpen((v) => !v)}>
          <span className={`text-white/40 transition-transform ${open ? "rotate-90" : ""}`}>▶</span>
          <span className="font-medium">{w.name}</span>
          <span className="ml-2 text-sm text-white/50">{w.cpuModel || "—"}{w.gpuModel ? ` · ${w.gpuModel}` : ""}</span>
        </button>
        <div className="flex items-center gap-4">
          <div className="text-right text-sm">
            <span className={STATUS_COLOR[w.status] ?? "text-white/50"}>{w.status}</span>
            {w.isVerified && <span className="ml-2 text-xs text-neon-blue">verified</span>}
            {w.benchmarkScore != null && <div className="text-xs text-white/40">bench {Number(w.benchmarkScore).toFixed(0)}</div>}
          </div>
          <button className="text-xs text-red-400/80 hover:text-red-400 disabled:opacity-40" disabled={deleting} onClick={onDelete}>Delete</button>
        </div>
      </div>

      {open && (
        <div className="mt-4 border-t border-white/10 pt-4">
          {isLoading || !s ? <p className="text-sm text-white/50">Loading stats…</p> : (
            <>
              <div className="grid gap-3 sm:grid-cols-4 text-sm">
                <Metric label="Online" value={s.online ? "yes" : "no"} accent={s.online ? "text-neon-green" : "text-white/40"} />
                <Metric label="Earning mode" value={s.earningMode} />
                <Metric label="Jobs done" value={String(s.jobs.completed)} />
                <Metric label="Jobs failed" value={String(s.jobs.failed)} accent={s.jobs.failed ? "text-red-400" : undefined} />
                <Metric label="Earnings" value={`$${s.earningsUsd.toFixed(4)}`} accent="text-neon-green" />
                <Metric label="Tokens served" value={s.tokensServed.toLocaleString()} />
                <Metric label="Benchmark" value={s.benchmarkScore != null ? s.benchmarkScore.toFixed(0) : "—"} />
                <Metric label="Last seen" value={fmtTime(s.lastSeenAt)} />
              </div>
              <div className="mt-3 grid gap-3 sm:grid-cols-2 text-xs text-white/50">
                <p>Hardware: {[s.hardware.cpuModel, s.hardware.gpuModel, s.hardware.ramGb ? `${s.hardware.ramGb}GB RAM` : null, s.hardware.os, s.hardware.region].filter(Boolean).join(" · ") || "—"}</p>
                <p>Models: {s.supportedModels.length ? s.supportedModels.join(", ") : "—"} · registered {fmtTime(s.createdAt)}</p>
              </div>

              <TokenBlock workerId={w.id} token={s.token} />

              {/* Rent out / hourly availability for this worker */}
              <div className="mt-3 rounded-lg border border-white/10 bg-black/20 p-3">
                {s.rental.listed ? (
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm">
                      <span className="text-neon-green">● Available for rent</span>
                      <span className="ml-2 text-white/60">at ${Number(s.rental.pricePerHourUsd).toFixed(2)}/hr</span>
                      <span className="ml-2 text-xs text-white/40">(rentable by the hour while online)</span>
                    </p>
                    <button className="text-xs text-red-400/80 hover:text-red-400 disabled:opacity-40" disabled={unlistMut.isPending} onClick={() => unlistMut.mutate()}>
                      {unlistMut.isPending ? "Unlisting…" : "Unlist"}
                    </button>
                  </div>
                ) : (
                  <div className="flex flex-wrap items-end gap-3">
                    <div>
                      <p className="text-sm font-medium">Rent this worker out</p>
                      <p className="text-xs text-white/40">Renters pay per hour; you keep 95% (5% marketplace fee).</p>
                    </div>
                    <label className="text-xs text-white/60">Price / hour (USD)
                      <input type="number" min="0" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)}
                        className="ml-0 mt-1 block w-28 rounded-lg border border-white/10 bg-black/30 px-2 py-1 text-sm" />
                    </label>
                    <button className="btn-primary disabled:opacity-40" disabled={listMut.isPending || !(Number(price) > 0)} onClick={() => listMut.mutate()}>
                      {listMut.isPending ? "Listing…" : "List for rent"}
                    </button>
                    {listMut.isError && <p className="w-full text-xs text-red-400">{(listMut.error as Error).message}</p>}
                  </div>
                )}
              </div>
              {s.recentJobs.length > 0 && (
                <div className="mt-3">
                  <p className="mb-1 text-xs text-white/50">Recent jobs</p>
                  <div className="overflow-x-auto rounded-lg border border-white/5">
                    <table className="w-full text-xs">
                      <thead className="bg-white/5 text-left text-white/40"><tr>
                        <th className="px-2 py-1">Kind</th><th className="px-2 py-1">Model</th><th className="px-2 py-1">Status</th><th className="px-2 py-1">Out tok</th><th className="px-2 py-1">Reward</th><th className="px-2 py-1">When</th>
                      </tr></thead>
                      <tbody>
                        {s.recentJobs.map((j) => (
                          <tr key={j.id} className="border-t border-white/5">
                            <td className="px-2 py-1">{j.kind}</td>
                            <td className="px-2 py-1 text-white/60">{j.model || "—"}</td>
                            <td className="px-2 py-1">{j.status}</td>
                            <td className="px-2 py-1">{j.outputTokens}</td>
                            <td className="px-2 py-1">{j.rewardUsd != null ? `$${Number(j.rewardUsd).toFixed(4)}` : "—"}</td>
                            <td className="px-2 py-1 text-white/40">{fmtTime(j.createdAt)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** Worker token, re-readable by the owner. Hidden by default; workers created
 *  before raw tokens were stored only have a hash — offer regeneration. */
function TokenBlock({ workerId, token }: { workerId: string; token: string | null }) {
  const qc = useQueryClient();
  const [show, setShow] = useState(false);
  const [copied, setCopied] = useState(false);
  const regen = useMutation({
    mutationFn: () => api<{ token: string }>(`/api/workers/${workerId}/token`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["worker-stats", workerId] }),
  });
  const copy = async (t: string) => {
    await navigator.clipboard.writeText(t);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="mt-3 rounded-lg border border-white/10 bg-black/20 p-3">
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm font-medium">Worker token</p>
        {token ? (
          <>
            <code className="break-all text-xs text-neon-green">{show ? token : `${token.slice(0, 15)}…${token.slice(-4)}`}</code>
            <button className="text-xs text-neon-blue hover:underline" onClick={() => setShow((v) => !v)}>{show ? "Hide" : "Reveal"}</button>
            <button className="text-xs text-neon-blue hover:underline" onClick={() => copy(token)}>{copied ? "Copied ✓" : "Copy"}</button>
          </>
        ) : (
          <>
            <span className="text-xs text-white/40">created before tokens were stored — regenerate to get one</span>
            <button
              className="text-xs text-neon-blue hover:underline disabled:opacity-40"
              disabled={regen.isPending}
              onClick={() => {
                if (confirm("Regenerate the token? The old token stops working immediately — re-run the installer (or update WORKER_TOKEN) on the rig.")) regen.mutate();
              }}
            >
              {regen.isPending ? "Regenerating…" : "Regenerate token"}
            </button>
          </>
        )}
      </div>
      <p className="mt-1 text-xs text-white/40">
        Used by the agent installer and the <code>animica bittensor … --token</code> CLI commands.
        {token && (
          <>
            {" "}Lost on the rig? <button className="text-neon-blue hover:underline" onClick={() => { if (confirm("Regenerate the token? The old token stops working immediately — re-run the installer (or update WORKER_TOKEN) on the rig.")) regen.mutate(); }}>Regenerate</button>
          </>
        )}
      </p>
      {regen.isError && <p className="mt-1 text-xs text-red-400">{(regen.error as Error).message}</p>}
    </div>
  );
}

function Metric({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-black/20 p-3">
      <p className="text-xs text-white/50">{label}</p>
      <p className={`mt-0.5 font-medium ${accent ?? "text-white"}`}>{value}</p>
    </div>
  );
}
