"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";

const ASSETS = ["ANM", "XMR", "SOL", "USDT", "BTC", "ORIGINAL_ASSET"];
const TYPES = ["worker_inference", "compute_rental", "mining", "referral"];

interface Wallet { id: string; asset: string; address: string; isDefault: boolean }
interface Req { id: string; type: string; asset: string; amountUsd: string; status: string; createdAt: string }

export default function PayoutsPage() {
  const qc = useQueryClient();
  const [wallet, setWallet] = useState({ asset: "USDT", address: "", isDefault: true });
  const [req, setReq] = useState({ type: "worker_inference", asset: "USDT", address: "", amountUsd: "" });

  const { data: bal } = useQuery({ queryKey: ["payable"], queryFn: () => api<{ payableUsd: number }>("/api/payouts/balance"), retry: false });
  const { data: wallets } = useQuery({ queryKey: ["wallets"], queryFn: () => api<Wallet[]>("/api/payouts/wallets"), retry: false });
  const { data: requests } = useQuery({ queryKey: ["payout-reqs"], queryFn: () => api<Req[]>("/api/payouts/requests"), retry: false });

  const addWallet = useMutation({ mutationFn: () => api("/api/payouts/wallets", { method: "POST", body: wallet }), onSuccess: () => qc.invalidateQueries({ queryKey: ["wallets"] }) });
  const createReq = useMutation({
    mutationFn: () => api("/api/payouts/requests", { method: "POST", body: { ...req, amountUsd: Number(req.amountUsd) } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["payout-reqs"] }); qc.invalidateQueries({ queryKey: ["payable"] }); },
  });

  const f = "field mt-1";
  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <h1 className="text-2xl font-semibold">Payouts</h1>
        <span className="text-sm text-white/60">Payable: <span className="text-neon-green">${(bal?.payableUsd ?? 0).toFixed(2)}</span></span>
      </div>
      <p className="text-sm text-white/50">Payouts require manual admin approval. Optional ANM payout is available for any earning type.</p>

      <div className="card space-y-3">
        <h2 className="font-medium text-neon-blue">Payout wallets</h2>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">Asset<select className={f} value={wallet.asset} onChange={(e) => setWallet({ ...wallet, asset: e.target.value })}>{ASSETS.map((a) => <option key={a}>{a}</option>)}</select></label>
          <label className="flex-1 text-sm">Address<input className={f} value={wallet.address} onChange={(e) => setWallet({ ...wallet, address: e.target.value })} /></label>
          <button className="btn-primary" onClick={() => addWallet.mutate()} disabled={addWallet.isPending || !wallet.address}>Add wallet</button>
        </div>
        <div className="space-y-1 text-sm text-white/70">{(wallets ?? []).map((w) => <div key={w.id}>{w.asset} · <span className="text-white/50">{w.address}</span>{w.isDefault && <span className="ml-2 text-xs text-neon-green">default</span>}</div>)}</div>
      </div>

      <div className="card space-y-3">
        <h2 className="font-medium text-neon-blue">Request a payout</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-sm">Type<select className={f} value={req.type} onChange={(e) => setReq({ ...req, type: e.target.value })}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select></label>
          <label className="text-sm">Asset<select className={f} value={req.asset} onChange={(e) => setReq({ ...req, asset: e.target.value })}>{ASSETS.map((a) => <option key={a}>{a}</option>)}</select></label>
          <label className="text-sm sm:col-span-2">Address<input className={f} value={req.address} onChange={(e) => setReq({ ...req, address: e.target.value })} /></label>
          <label className="text-sm">Amount (USD)<input className={f} type="number" value={req.amountUsd} onChange={(e) => setReq({ ...req, amountUsd: e.target.value })} /></label>
        </div>
        <button className="btn-primary" onClick={() => createReq.mutate()} disabled={createReq.isPending || !req.address || !req.amountUsd}>Request payout</button>
        {createReq.isError && <p className="text-sm text-red-400">{(createReq.error as Error).message}</p>}
      </div>

      <div className="space-y-2">
        <h2 className="font-medium">Requests</h2>
        {(requests ?? []).map((r) => (
          <div key={r.id} className="card flex items-center justify-between text-sm">
            <span>{r.type} · ${Number(r.amountUsd).toFixed(2)} {r.asset}</span>
            <span className="text-white/50">{r.status}</span>
          </div>
        ))}
        {(requests ?? []).length === 0 && <p className="text-white/60">No payout requests yet.</p>}
      </div>
    </div>
  );
}
