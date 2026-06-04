"use client";

import { useQuery, useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";

interface Listing {
  id: string; providerType: string; title: string; description?: string;
  cpuModel?: string; gpuModel?: string; vramGb?: number; ramGb?: number; region?: string;
  pricePerHourUsd: string; supportedModels: string[];
}

export function RentListings({ providerType, title }: { providerType?: string; title: string }) {
  const qs = providerType ? `?providerType=${providerType}` : "";
  const { data: listings, isLoading } = useQuery({ queryKey: ["listings", providerType], queryFn: () => api<Listing[]>(`/api/rentals/listings${qs}`) });
  const [hours, setHours] = useState<Record<string, number>>({});
  const [msg, setMsg] = useState<string | null>(null);

  const rent = useMutation({
    mutationFn: (v: { listingId: string; hours: number; payWith: "credits" | "crypto" }) =>
      api<{ order: { id: string; status: string; endpointUrl?: string }; invoiceUrl?: string }>("/api/rentals/orders", { method: "POST", body: v }),
    onSuccess: (d) => {
      if (d.invoiceUrl) { window.location.href = d.invoiceUrl; return; }
      setMsg(`Rental ${d.order.id.slice(0, 8)} is ${d.order.status}. Endpoint: ${d.order.endpointUrl ?? "provisioning…"}`);
    },
    onError: (e) => setMsg((e as Error).message),
  });

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-semibold">{title}</h1>
      {msg && <p className="card text-sm text-neon-green">{msg}</p>}
      {isLoading ? <p className="text-white/60">Loading…</p> : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {(listings ?? []).map((l) => {
            const h = hours[l.id] ?? 1;
            const total = (Number(l.pricePerHourUsd) * h).toFixed(2);
            return (
              <div key={l.id} className="card flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <h3 className="font-medium">{l.title}</h3>
                  <span className="text-xs uppercase text-neon-violet">{l.providerType}</span>
                </div>
                <p className="text-sm text-white/60">{l.gpuModel || l.cpuModel || l.description || "—"}{l.vramGb ? ` · ${l.vramGb}GB VRAM` : ""}{l.region ? ` · ${l.region}` : ""}</p>
                <p className="text-lg font-semibold text-neon-blue">${l.pricePerHourUsd}<span className="text-sm text-white/50">/hr</span></p>
                <div className="mt-auto flex items-center gap-2">
                  <input type="number" min={1} value={h} onChange={(e) => setHours({ ...hours, [l.id]: Math.max(1, Number(e.target.value)) })} className="w-16 rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-sm" />
                  <span className="text-sm text-white/60">= ${total}</span>
                </div>
                <div className="flex gap-2">
                  <button className="btn-primary flex-1 text-sm" onClick={() => rent.mutate({ listingId: l.id, hours: h, payWith: "credits" })} disabled={rent.isPending}>Rent (credits)</button>
                  <button className="btn-ghost text-sm" onClick={() => rent.mutate({ listingId: l.id, hours: h, payWith: "crypto" })} disabled={rent.isPending}>Crypto</button>
                </div>
                {l.supportedModels?.length > 0 && <p className="text-xs text-white/40">models: {l.supportedModels.join(", ")}</p>}
              </div>
            );
          })}
          {(listings ?? []).length === 0 && <p className="text-white/60">No listings available.</p>}
        </div>
      )}
    </div>
  );
}
