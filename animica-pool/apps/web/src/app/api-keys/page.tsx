"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";

interface Key {
  id: string;
  prefix: string;
  label?: string;
  isActive: boolean;
  createdAt: string;
}

export default function ApiKeysPage() {
  const qc = useQueryClient();
  const [label, setLabel] = useState("");
  const [reveal, setReveal] = useState<string | null>(null);

  const { data: keys, isLoading } = useQuery({
    queryKey: ["api-keys"],
    queryFn: () => api<Key[]>("/api/api-keys"),
    retry: false,
  });

  const create = useMutation({
    mutationFn: () => api<{ key: string }>("/api/api-keys", { method: "POST", body: { label } }),
    onSuccess: (d) => {
      setReveal(d.key);
      setLabel("");
      qc.invalidateQueries({ queryKey: ["api-keys"] });
    },
  });

  const revoke = useMutation({
    mutationFn: (id: string) => api(`/api/api-keys/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys"] }),
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">API keys</h1>

      <div className="card flex items-end gap-3">
        <label className="flex-1 text-sm">
          Label
          <input className="field mt-1" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="my app" />
        </label>
        <button className="btn-primary" onClick={() => create.mutate()} disabled={create.isPending}>
          {create.isPending ? "…" : "Create key"}
        </button>
      </div>

      {reveal && (
        <div className="card border-neon-green/40">
          <p className="text-sm text-white/70">Copy this now — it’s shown only once:</p>
          <code className="mt-2 block break-all text-neon-green">{reveal}</code>
        </div>
      )}

      {isLoading ? (
        <p className="text-white/60">Loading…</p>
      ) : (
        <div className="space-y-2">
          {(keys ?? []).map((k) => (
            <div key={k.id} className="card flex items-center justify-between">
              <div>
                <code className="text-white/80">{k.prefix}…</code>
                <span className="ml-3 text-sm text-white/50">{k.label}</span>
              </div>
              <button className="btn-ghost text-sm" onClick={() => revoke.mutate(k.id)}>Revoke</button>
            </div>
          ))}
          {(keys ?? []).length === 0 && <p className="text-white/60">No keys yet.</p>}
        </div>
      )}
    </div>
  );
}
