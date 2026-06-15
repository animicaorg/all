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
    <div className="space-y-10">
      <header className="space-y-4">
        <span className="badge">Billing</span>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <h1 className="text-4xl font-semibold tracking-tightest text-white md:text-5xl">
            Buy AI <span className="grad-text">credits</span>
          </h1>
          <span className="rounded-xl border border-white/10 bg-white/[0.04] px-4 py-2 text-sm text-white/60 backdrop-blur">
            Balance <span className="ml-1 font-semibold text-neon-green">${(balance?.balanceUsd ?? 0).toFixed(2)}</span>
          </span>
        </div>
        <p className="max-w-2xl text-lg text-white/65">
          Top up once and spend across every model. 1 USD = 1 credit, billed per token at the rates on the AI page.
        </p>
      </header>

      <section className="space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-neon-green/80">Packages</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {(packages ?? []).map((p) => (
            <div key={p.id} className="card flex flex-col gap-4">
              <div className="space-y-1">
                <h3 className="text-sm font-medium text-white/70">{p.label}</h3>
                <p className="text-3xl font-semibold text-white">
                  <span className="text-white/40">$</span>{p.amountUsd}
                </p>
              </div>
              <button className="btn-primary mt-auto" onClick={() => checkout.mutate({ packageId: p.id })} disabled={checkout.isPending}>
                Buy
              </button>
            </div>
          ))}
        </div>
      </section>

      <section className="card flex flex-wrap items-end gap-4">
        <label className="flex-1 text-sm text-white/70">
          Custom amount (USD)
          <input className="field mt-1.5" type="number" min={1} value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="100" />
        </label>
        <button className="btn-secondary" disabled={checkout.isPending || !custom} onClick={() => checkout.mutate({ amountUsd: Number(custom) })}>
          Buy custom
        </button>
      </section>

      <p className="text-sm text-white/40">
        Checkout is handled securely via NOWPayments. Credits land in your balance once the payment confirms.
      </p>

      {checkout.isError && <p className="text-sm text-red-400">{(checkout.error as Error).message}</p>}
    </div>
  );
}
