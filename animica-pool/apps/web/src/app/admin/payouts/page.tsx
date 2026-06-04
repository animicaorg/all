"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";

interface Req { id: string; type: string; asset: string; amountUsd: string; status: string; address: string; user?: { email: string } }

export default function AdminPayoutsPage() {
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const [picked, setPicked] = useState<Record<string, boolean>>({});

  const { data: reqs, error } = useQuery({ queryKey: ["admin-payouts"], queryFn: () => api<Req[]>("/api/admin/payouts"), retry: false });

  const act = useMutation({
    mutationFn: (v: { id: string; action: "approve" | "reject" }) => api(`/api/admin/payouts/${v.id}/${v.action}`, { method: "POST", body: {} }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-payouts"] }),
  });
  const batch = useMutation({
    mutationFn: () => api<{ submitted: boolean; note?: string }>("/api/admin/payout-batches", { method: "POST", body: { requestIds: Object.keys(picked).filter((k) => picked[k]) } }),
    onSuccess: (d) => { setMsg(d.note || (d.submitted ? "Batch submitted to NOWPayments." : "Batch recorded.")); qc.invalidateQueries({ queryKey: ["admin-payouts"] }); },
    onError: (e) => setMsg((e as Error).message),
  });

  if (error) return <p className="card">Admin only.</p>;

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-semibold">Admin · payouts</h1>
      <p className="text-sm text-white/50">Approve requests, then batch the approved ones. Payouts only submit to NOWPayments when NOWPAYMENTS_PAYOUTS_ENABLED=true — otherwise the batch is recorded for manual processing.</p>
      {msg && <p className="card text-sm text-neon-green">{msg}</p>}
      <button className="btn-primary" onClick={() => batch.mutate()} disabled={batch.isPending}>Create batch from selected (approved)</button>
      <div className="space-y-2">
        {(reqs ?? []).map((r) => (
          <div key={r.id} className="card flex items-center justify-between text-sm">
            <div className="flex items-center gap-3">
              {r.status === "approved" && <input type="checkbox" checked={!!picked[r.id]} onChange={(e) => setPicked({ ...picked, [r.id]: e.target.checked })} />}
              <span>{r.user?.email} · {r.type} · ${Number(r.amountUsd).toFixed(2)} {r.asset}</span>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-white/50">{r.status}</span>
              {(r.status === "pending_review" || r.status === "pending") && (
                <>
                  <button className="text-xs text-neon-green" onClick={() => act.mutate({ id: r.id, action: "approve" })}>Approve</button>
                  <button className="text-xs text-red-400" onClick={() => act.mutate({ id: r.id, action: "reject" })}>Reject</button>
                </>
              )}
            </div>
          </div>
        ))}
        {(reqs ?? []).length === 0 && <p className="text-white/60">No payout requests.</p>}
      </div>
    </div>
  );
}
