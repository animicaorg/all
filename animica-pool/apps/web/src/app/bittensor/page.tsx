"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface Overview {
  demandSide: { provider: string; enabled: boolean };
  supplySide: {
    netuid: number; miningEnabled: boolean; minerStatus: string; hotkeySet: boolean;
    executors: Record<string, number>;
    ownerSharePercent: number; holdbackDays: number;
    eligibility: { minUptimePct: number; minHistoryDays: number; minVramGb: number };
  };
  earnings: { totalUsd: number; ownerPaidUsd: number; totalTao: number };
  treasury: { targetUsd: number; accruedUsd: number; pct: number; funded: boolean };
  poller: { configured: boolean; intervalMin: number; last: { ok?: boolean; at?: string; alphaStake?: number; recordedUsd?: number; skipped?: string } | null };
}

interface Earning {
  id: string; kind: string; alphaAmount: number; taoAmount: number; usdValue: number;
  ownerShareUsd: number; poolShareUsd: number; createdAt: string;
}

interface MyExecutor {
  id: string; status: string; gpuType: string; gpuCount: number;
  miner: { netuid: number; status: string };
  earnings: { releasedUsd: number; heldUsd: number; holdbackDays: number };
}

interface MyWorker { id: string; name: string; status: string; gpuModel: string | null; token: string | null }

export default function BittensorPage() {
  const { data: ov } = useQuery({ queryKey: ["bt-overview"], queryFn: () => api<Overview>("/api/bittensor/overview"), refetchInterval: 60_000 });
  const { data: earnings } = useQuery({ queryKey: ["bt-earnings"], queryFn: () => api<Earning[]>("/api/bittensor/earnings") });
  const { data: myExecutors } = useQuery({ queryKey: ["bt-my-executors"], queryFn: () => api<MyExecutor[]>("/api/bittensor/executors"), retry: false });
  const { data: myWorkers } = useQuery({ queryKey: ["my-workers"], queryFn: () => api<MyWorker[]>("/api/workers"), retry: false });

  const supplyLive = ov?.supplySide.miningEnabled && ov?.supplySide.hotkeySet;
  const executorTotal = ov ? Object.values(ov.supplySide.executors).reduce((s, n) => s + n, 0) : 0;

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <h1 className="text-2xl font-semibold">Bittensor compute layer</h1>
        <p className="max-w-3xl text-white/60">
          Animica Pool is a compute, mining and inference layer on{" "}
          <a className="text-neon-blue hover:underline" href="https://bittensor.com" target="_blank" rel="noreferrer">Bittensor</a>.
          Our AI API buys inference from Bittensor subnet miners (Chutes · SN64) — and your GPU rigs can earn TAO
          by backing our miner on the Celium GPU marketplace (SN51), paid out in ANM, XMR, SOL, BTC or USDT.
        </p>
        {ov && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Inference via Bittensor" value={ov.demandSide.enabled ? "LIVE" : "off"} accent={ov.demandSide.enabled} />
            <Stat label={`GPU pool (SN${ov.supplySide.netuid})`} value={supplyLive ? "LIVE" : "warming up"} accent={!!supplyLive} />
            <Stat label="Pool earnings (TAO side)" value={`$${ov.earnings.totalUsd.toFixed(2)}`} />
            <Stat label="Enrolled rigs" value={String(executorTotal)} />
          </div>
        )}
      </section>

      {ov && !supplyLive && (
        <section className="card space-y-3">
          <h2 className="font-medium text-neon-blue">Registration countdown — funded by AI margin</h2>
          <p className="text-sm text-white/70">
            Every AI request served through the pool sets aside a treasury slice that funds our Bittensor SN{ov.supplySide.netuid} miner
            registration (a one-time on-chain burn) plus the first rig collateral. When the bar fills, the GPU pool flips live —
            rigs enrolled now start earning from day one.
          </p>
          <div className="h-3 w-full overflow-hidden rounded-full bg-black/40">
            <div className="h-full rounded-full bg-neon-green transition-all" style={{ width: `${Math.max(1, ov.treasury.pct)}%` }} />
          </div>
          <p className="text-sm text-white/60">
            ${ov.treasury.accruedUsd.toFixed(2)} / ${ov.treasury.targetUsd.toFixed(0)} ({ov.treasury.pct.toFixed(1)}%)
          </p>
        </section>
      )}

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="card space-y-2">
          <h2 className="font-medium text-neon-blue">Use it — AI inference backed by Bittensor</h2>
          <p className="text-sm text-white/70">
            The OpenAI-compatible API routes <code className="text-neon-green">anm-*</code> models to Bittensor subnet miners first,
            with automatic fallback. Same endpoint, same key, decentralized backend.
          </p>
          <pre className="overflow-x-auto rounded-lg bg-black/40 p-3 text-xs text-neon-green">{`curl https://pool.animica.org/v1/chat/completions \\
  -H "Authorization: Bearer <your-api-key>" \\
  -d '{"model":"anm-pro-70b","messages":[{"role":"user","content":"hi"}]}'`}</pre>
          <p className="text-sm text-white/60">Get a key on the <a className="text-neon-blue hover:underline" href="/api-keys">API keys</a> page, top up <a className="text-neon-blue hover:underline" href="/credits">credits</a>, try it in the <a className="text-neon-blue hover:underline" href="/ai">playground</a>.</p>
        </div>

        <div className="card space-y-2">
          <h2 className="font-medium text-neon-blue">Earn with your GPU — back our SN51 miner</h2>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-white/70">
            <li><code className="text-neon-green">pip install --upgrade animica</code></li>
            <li>Create a worker on the <a className="text-neon-blue hover:underline" href="/workers">Workers</a> page and install the agent on your rig (GPU is auto-detected).</li>
            <li>Build uptime history — rigs need {ov?.supplySide.eligibility.minUptimePct ?? 97}% uptime over {ov?.supplySide.eligibility.minHistoryDays ?? 7} days and an NVIDIA GPU with ≥{ov?.supplySide.eligibility.minVramGb ?? 16} GB VRAM (Bittensor slashes flaky rigs, so we gate hard; Apple/AMD GPUs can't serve SN51&apos;s CUDA executors).</li>
            <li>Enroll + provision from the rig itself:</li>
          </ol>
          <EnrollCommands workers={myWorkers} />
          <p className="text-sm text-white/60">
            You keep {ov?.supplySide.ownerSharePercent ?? 70}% of what your rig earns; the last {ov?.supplySide.holdbackDays ?? 7} days
            stay held back as slash protection, then release to your <a className="text-neon-blue hover:underline" href="/payouts">payouts</a> balance.
            CPU-only rig? Keep <a className="text-neon-blue hover:underline" href="/mine">dual-mining ANM + XMR</a> — Bittensor compute subnets are GPU-only.
          </p>
        </div>
      </section>

      {myExecutors && myExecutors.length > 0 && (
        <section className="card space-y-3">
          <h2 className="font-medium text-neon-blue">Your rigs in the GPU pool</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-white/50">
                <tr><th className="p-2">GPU</th><th className="p-2">Status</th><th className="p-2">Released</th><th className="p-2">Held (slash buffer)</th></tr>
              </thead>
              <tbody>
                {myExecutors.map((ex) => (
                  <tr key={ex.id} className="border-t border-white/5">
                    <td className="p-2">{ex.gpuType}{ex.gpuCount > 1 ? ` ×${ex.gpuCount}` : ""}</td>
                    <td className="p-2"><StatusPill status={ex.status} /></td>
                    <td className="p-2 text-neon-green">${ex.earnings.releasedUsd.toFixed(4)}</td>
                    <td className="p-2 text-white/60">${ex.earnings.heldUsd.toFixed(4)} ({ex.earnings.holdbackDays}d)</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {myWorkers && myWorkers.length > 0 && <EnrollSection workers={myWorkers} />}

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="card space-y-2">
          <h2 className="font-medium text-neon-blue">On-chain earnings feed</h2>
          {ov?.poller && (
            <p className="text-xs text-white/50">
              Poller: {ov.poller.configured ? `every ${ov.poller.intervalMin} min` : "awaiting miner registration"}
              {ov.poller.last?.at ? ` · last ${new Date(ov.poller.last.at).toLocaleString()} (${ov.poller.last.ok ? "ok" : ov.poller.last.skipped ?? "error"})` : ""}
            </p>
          )}
          {earnings && earnings.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-white/50">
                  <tr><th className="p-2">When</th><th className="p-2">Kind</th><th className="p-2">TAO</th><th className="p-2">USD</th><th className="p-2">To rigs</th></tr>
                </thead>
                <tbody>
                  {earnings.slice(0, 10).map((e) => (
                    <tr key={e.id} className="border-t border-white/5">
                      <td className="p-2 text-white/60">{new Date(e.createdAt).toLocaleDateString()}</td>
                      <td className="p-2">{e.kind}</td>
                      <td className="p-2">{e.taoAmount.toFixed(4)}</td>
                      <td className="p-2 text-neon-green">${e.usdValue.toFixed(4)}</td>
                      <td className="p-2 text-white/60">${e.ownerShareUsd.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-white/50">No TAO earnings yet — they appear here automatically once the SN51 miner is registered and rigs are live.</p>
          )}
        </div>

        <div className="card space-y-2">
          <h2 className="font-medium text-neon-blue">How the pool splits Bittensor earnings</h2>
          <ul className="list-disc space-y-1 pl-5 text-sm text-white/70">
            <li><span className="text-white">{ov?.supplySide.ownerSharePercent ?? 70}% to rig owners</span> — released after the {ov?.supplySide.holdbackDays ?? 7}-day holdback, withdrawable in ANM/XMR/SOL/BTC/USDT.</li>
            <li><span className="text-white">{100 - (ov?.supplySide.ownerSharePercent ?? 70)}% to the pool</span> — covers registration burns, executor collateral and ops; flows through the public <a className="text-neon-blue hover:underline" href="/stats">revenue split</a>.</li>
            <li><span className="text-white">Slash buffer</span> — Bittensor burns collateral if a rented GPU drops mid-rental; your held-back week absorbs it first, so keep rigs online.</li>
            <li><span className="text-white">Earnings are on-chain</span> — emissions accrue as alpha stake to the pool hotkey on SN{ov?.supplySide.netuid ?? 51}; the poller converts deltas to USD at live prices.</li>
          </ul>
        </div>
      </section>
    </div>
  );
}

/** Enroll command snippet — substitutes the signed-in user's real worker token
 *  (the workers fetch includes it) so the commands are copy-paste ready. */
function EnrollCommands({ workers }: { workers?: MyWorker[] }) {
  const withToken = (workers ?? []).filter((w) => w.token);
  // GPU rigs first — they're the ones that can actually enroll.
  withToken.sort((a, b) => Number(!!b.gpuModel) - Number(!!a.gpuModel));
  const [sel, setSel] = useState<string | null>(null);
  const chosen = withToken.find((w) => w.id === sel) ?? withToken[0];
  const token = chosen?.token ?? "anm_worker_…";
  return (
    <div className="space-y-1">
      {withToken.length > 1 && (
        <label className="block text-xs text-white/50">Worker
          <select className="ml-2 rounded border border-white/10 bg-black/30 px-1 py-0.5 text-xs" value={chosen.id} onChange={(e) => setSel(e.target.value)}>
            {withToken.map((w) => <option key={w.id} value={w.id}>{w.name}{w.gpuModel ? ` · ${w.gpuModel}` : ""}</option>)}
          </select>
        </label>
      )}
      <pre className="overflow-x-auto rounded-lg bg-black/40 p-3 text-xs text-neon-green">{`animica bittensor status --token ${token}   # eligibility check
animica bittensor enroll --token ${token}   # join the GPU pool
animica bittensor up --token ${token}       # install SN51 executor`}</pre>
      {chosen ? (
        <p className="text-xs text-white/40">Token shown for <span className="text-white/60">{chosen.name}</span> — manage tokens on the <a className="text-neon-blue hover:underline" href="/workers">Workers</a> page.</p>
      ) : (
        <p className="text-xs text-white/40">Sign in to see your worker token filled in here, or copy it from the <a className="text-neon-blue hover:underline" href="/workers">Workers</a> page.</p>
      )}
    </div>
  );
}

// Mirrors the server's CUDA gate (bittensor.service isCudaGpu) so we don't
// offer SN51 enrollment for GPUs that would be rejected anyway.
function isCudaGpu(model: string | null): boolean {
  if (!model) return false;
  if (/apple|radeon|\bamd\b|intel|\barc\b/i.test(model)) return false;
  return /nvidia|geforce|rtx|gtx|tesla|quadro|titan|\b[ahlvb]\d{2,3}\b/i.test(model);
}

function EnrollSection({ workers }: { workers: MyWorker[] }) {
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const enroll = useMutation({
    mutationFn: (workerId: string) => api("/api/bittensor/executors/enroll", { method: "POST", body: { workerId } }),
    onSuccess: () => { setMsg("Enrolled — provisioning instructions are on your rig via `animica bittensor up`."); qc.invalidateQueries({ queryKey: ["bt-my-executors"] }); },
    onError: (e: Error) => setMsg(e.message),
  });
  const gpuWorkers = workers.filter((w) => w.gpuModel);
  if (gpuWorkers.length === 0) return null;
  const cudaWorkers = gpuWorkers.filter((w) => isCudaGpu(w.gpuModel));
  const otherGpus = gpuWorkers.filter((w) => !isCudaGpu(w.gpuModel));
  return (
    <section className="card space-y-3">
      <h2 className="font-medium text-neon-blue">Enroll a rig</h2>
      {cudaWorkers.length > 0 && (
        <div className="flex flex-wrap gap-3">
          {cudaWorkers.map((w) => (
            <button key={w.id} onClick={() => enroll.mutate(w.id)} disabled={enroll.isPending}
              className="rounded-lg border border-neon-blue/40 bg-neon-blue/10 px-4 py-2 text-sm hover:bg-neon-blue/20 disabled:opacity-50">
              {w.name} · {w.gpuModel}
            </button>
          ))}
        </div>
      )}
      {otherGpus.length > 0 && (
        <p className="text-xs text-white/40">
          Can&apos;t enroll: {otherGpus.map((w) => `${w.name} (${w.gpuModel})`).join(", ")} — SN51 executors need an NVIDIA
          CUDA GPU. These rigs still earn via <a className="text-neon-blue hover:underline" href="/workers">inference jobs</a> and{" "}
          <a className="text-neon-blue hover:underline" href="/mine">mining</a>.
        </p>
      )}
      {msg && <p className="text-sm text-white/60">{msg}</p>}
    </section>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="card">
      <p className="text-sm text-white/50">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${accent ? "text-neon-green" : "text-neon-blue"}`}>{value}</p>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const color = status === "active" ? "text-neon-green border-neon-green/40" : status === "offline" || status === "suspended" ? "text-red-400 border-red-400/40" : "text-white/60 border-white/20";
  return <span className={`rounded-full border px-2 py-0.5 text-xs ${color}`}>{status}</span>;
}
