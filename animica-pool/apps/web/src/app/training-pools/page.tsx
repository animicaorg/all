"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

// ENA coordinator is proxied at /api/ena/* (nginx → :8791).
async function getJson<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(`/api/ena${path}`, { credentials: "omit" });
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}
async function postJson(path: string, body: any): Promise<{ ok: boolean; j: any }> {
  const r = await fetch(`/api/ena${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  let j: any = null;
  try { j = await r.json(); } catch {}
  return { ok: r.ok, j };
}

function getProvider(timeoutMs = 1800): Promise<any> {
  return new Promise((resolve) => {
    const w = window as any;
    if (w.animica?.request) return resolve(w.animica);
    let done = false;
    const settle = () => { if (!done) { done = true; resolve((window as any).animica?.request ? (window as any).animica : null); } };
    window.addEventListener("animica#initialized", settle, { once: true });
    setTimeout(settle, timeoutMs);
  });
}

export default function TrainingPoolsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["ena-pools"],
    queryFn: () => getJson<any>("/pool/list"),
    refetchInterval: 20000,
  });
  const pools: any[] = data?.pools ?? [];
  const [sel, setSel] = useState<string>("");
  const pid = sel || pools[0]?.pool_id || "";
  const { data: status } = useQuery({
    queryKey: ["ena-pool-status", pid],
    queryFn: () => (pid ? getJson<any>(`/pool/status?pool_id=${encodeURIComponent(pid)}`) : null),
    enabled: !!pid,
    refetchInterval: 15000,
  });

  const [amount, setAmount] = useState("1.0");
  const [acct, setAcct] = useState<string | null>(null);
  const [msg, setMsg] = useState<string>("");

  async function connect() {
    const prov = await getProvider();
    if (!prov) { setMsg("Install the Animica wallet extension to fund a pool."); return; }
    try {
      const accts = await prov.request({ method: "animica_requestAccounts" });
      setAcct(accts?.[0] ?? null);
    } catch { setMsg("Wallet connection rejected."); }
  }

  async function poll(poolId: string, txid: string, tries = 0): Promise<void> {
    const { j } = await postJson("/pool/fund/confirm", { pool_id: poolId, txid });
    if (j?.funded) { setMsg("Funded ✓ — thanks for accelerating the model."); return; }
    if (tries < 30) { await new Promise((r) => setTimeout(r, 5000)); return poll(poolId, txid, tries + 1); }
    setMsg("Not funded: " + (j?.reason || "unknown"));
  }

  async function fund() {
    if (!pid) { setMsg("Pick a pool first."); return; }
    const amt = parseFloat(amount);
    if (!(amt > 0)) { setMsg("Enter an amount greater than 0."); return; }
    let a = acct;
    if (!a) { await connect(); a = (window as any).animica && acct; if (!a) return; }
    setMsg("Requesting a quote…");
    const { ok, j: q } = await postJson("/pool/fund/quote", { pool_id: pid, reward_anm: amt, requester: a });
    if (!ok) { setMsg("Quote failed: " + (q?.error || q?.reason || "error")); return; }
    setMsg("Approve the payment in your wallet…");
    const prov = await getProvider();
    if (!prov) { setMsg("No wallet."); return; }
    try {
      const txid = await prov.request({
        method: "animica_sendTransaction",
        params: [{ from: a, to: q.treasury_address, value: String(q.required_nano), memo: q.memo }],
      });
      setMsg("Payment sent — verifying on-chain…");
      await poll(pid, String(txid));
    } catch (e: any) { setMsg("Error: " + (e?.message || e)); }
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold">Training <span className="text-neon-green">pools</span></h1>
        <p className="mt-1 max-w-2xl text-white/60">
          Fund a pool with ANM to accelerate the one global model. Trainers and servers earn
          from your contribution — every reward in ANM, receipted on-chain.
        </p>
      </div>

      {isLoading ? (
        <p className="text-white/60">Loading pools…</p>
      ) : pools.length === 0 ? (
        <p className="text-white/60">No pools yet — check back soon, or start one with <code className="text-neon-green">animica ena pool create</code>.</p>
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-xl border border-white/10 bg-white/5 p-5">
            <label className="block text-sm text-white/60">Pool</label>
            <select value={pid} onChange={(e) => setSel(e.target.value)}
              className="mt-1 w-full rounded-lg border border-white/10 bg-black/40 p-2">
              {pools.map((p) => <option key={p.pool_id} value={p.pool_id}>{p.name || p.pool_id}</option>)}
            </select>
            <label className="mt-4 block text-sm text-white/60">Fund amount (ANM)</label>
            <input value={amount} onChange={(e) => setAmount(e.target.value)} type="number" min="0.1" step="0.1"
              className="mt-1 w-full rounded-lg border border-white/10 bg-black/40 p-2" />
            <div className="mt-4 flex items-center gap-3">
              <button onClick={connect} className="text-white/70 hover:text-white">
                {acct ? `✓ ${acct.slice(0, 8)}…${acct.slice(-4)}` : "Connect wallet"}
              </button>
              <button onClick={fund} className="btn-primary ml-auto">Pay &amp; fund pool</button>
            </div>
            {msg && <p className="mt-3 text-sm text-white/70">{msg}</p>}
          </div>

          <div className="rounded-xl border border-white/10 bg-white/5 p-5">
            <h3 className="font-semibold">Pool status</h3>
            <table className="mt-3 w-full text-sm">
              <tbody>
                {status ? Object.entries({
                  "pool": status.name, "status": status.status, "round": status.round,
                  "shards (total)": status.num_shards,
                  "shards": fmtMap(status.shards), "contributions": fmtMap(status.contributions),
                  "budget (ANM)": status.budget_anm,
                  "served checkpoint": status.served_checkpoint
                    ? `round ${status.served_checkpoint.round} · ${String(status.served_checkpoint.checkpoint_hash || "").slice(0, 12)}…`
                    : "—",
                }).map(([k, v]) => (
                  <tr key={k} className="border-t border-white/5">
                    <td className="py-1 pr-4 font-mono text-white/50">{k}</td>
                    <td className="py-1">{String(v ?? "—")}</td>
                  </tr>
                )) : <tr><td className="text-white/50">Select a pool…</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function fmtMap(m: any): string {
  if (!m || typeof m !== "object") return "—";
  const ks = Object.keys(m);
  return ks.length ? ks.map((k) => `${k}: ${m[k]}`).join(", ") : "none yet";
}
