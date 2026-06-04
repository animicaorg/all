"use client";

import { useQuery, useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";

interface Pkg { id: string; label: string; amountUsd: number }

export default function CreditsPage() {
  const [custom, setCustom] = useState("");
  const { data: balance } = useQuery({ queryKey: ["balance"], queryFn: () => api<{ balanceUsd: number }>("/api/credits/balance"), retry: false });
  const { data: packages } = useQuery({ queryKey: ["packages"], queryFn: () => api<Pkg[]>("/api/credits/packages") });

  const checkout = useMutation({
    mutationFn: (body: { packageId?: string; amountUsd?: number }) =>
      api<{ invoiceUrl: string }>("/api/credits/checkout", { method: "POST", body }),
    onSuccess: (d) => { if (d.invoiceUrl) window.location.href = d.invoiceUrl; },
  });

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <h1 className="text-2xl font-semibold">Buy AI credits</h1>
        <span className="text-sm text-white/60">Balance: <span className="text-neon-green">${(balance?.balanceUsd ?? 0).toFixed(2)}</span></span>
      </div>
      <p className="text-white/60">Pay with crypto via NOWPayments. Credits are added once the payment confirms on-chain. 1 USD = 1 credit.</p>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {(packages ?? []).map((p) => (
          <div key={p.id} className="card flex flex-col gap-3">
            <div>
              <h3 className="font-medium">{p.label}</h3>
              <p className="text-2xl font-semibold text-neon-blue">${p.amountUsd}</p>
            </div>
            <button className="btn-primary mt-auto" onClick={() => checkout.mutate({ packageId: p.id })} disabled={checkout.isPending}>
              Buy
            </button>
          </div>
        ))}
      </div>

      <div className="card flex items-end gap-3">
        <label className="flex-1 text-sm">
          Custom amount (USD)
          <input className="field mt-1" type="number" min={1} value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="100" />
        </label>
        <button className="btn-ghost" disabled={checkout.isPending || !custom} onClick={() => checkout.mutate({ amountUsd: Number(custom) })}>
          Buy custom
        </button>
      </div>

      {checkout.isError && <p className="text-sm text-red-400">{(checkout.error as Error).message}</p>}
    </div>
  );
}
