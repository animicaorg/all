"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface Provider {
  name: string; capableEnv: boolean; enabled: boolean; priority: number;
  avgLatencyMs: number; successRate: number; currentQueue: number; lastError: string | null;
}

export default function ProvidersPage() {
  const qc = useQueryClient();
  const { data: providers, isLoading } = useQuery({ queryKey: ["providers"], queryFn: () => api<Provider[]>("/api/providers") });
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: () => api<{ user: { role: string } | null }>("/api/auth/me"), retry: false });
  const isAdmin = me?.user?.role === "ADMIN";

  const toggle = useMutation({
    mutationFn: (v: { name: string; isEnabled: boolean }) => api(`/api/admin/providers/${v.name}`, { method: "PATCH", body: { isEnabled: v.isEnabled } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });
  const test = useMutation({
    mutationFn: (name: string) => api(`/api/providers/${name}/test`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-semibold">Provider health</h1>
      <p className="text-white/60">Live routing providers — latency, success rate, queue depth, priority. Bittensor is a first-class route.</p>
      {isLoading ? <p className="text-white/60">Loading…</p> : (
        <div className="overflow-x-auto rounded-xl border border-white/10">
          <table className="w-full text-sm">
            <thead className="bg-white/5 text-left text-white/50">
              <tr>
                <th className="px-3 py-2">Provider</th><th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Priority</th><th className="px-3 py-2">Latency</th>
                <th className="px-3 py-2">Success</th><th className="px-3 py-2">Queue</th>
                {isAdmin && <th className="px-3 py-2">Admin</th>}
              </tr>
            </thead>
            <tbody>
              {(providers ?? []).map((p) => {
                const live = p.enabled && p.capableEnv;
                return (
                  <tr key={p.name} className="border-t border-white/5">
                    <td className="px-3 py-2 font-medium">{p.name}{p.name === "bittensor" && <span className="ml-2 text-xs text-neon-violet">first-class</span>}</td>
                    <td className="px-3 py-2"><span className={live ? "text-neon-green" : p.enabled ? "text-yellow-400" : "text-white/40"}>{p.enabled ? (p.capableEnv ? "live" : "no creds") : "disabled"}</span></td>
                    <td className="px-3 py-2">{p.priority}</td>
                    <td className="px-3 py-2">{p.avgLatencyMs} ms</td>
                    <td className="px-3 py-2">{p.successRate.toFixed(0)}%</td>
                    <td className="px-3 py-2">{p.currentQueue}</td>
                    {isAdmin && (
                      <td className="px-3 py-2 space-x-2">
                        <button className="text-xs text-neon-blue" onClick={() => toggle.mutate({ name: p.name, isEnabled: !p.enabled })}>{p.enabled ? "Disable" : "Enable"}</button>
                        <button className="text-xs text-white/60" onClick={() => test.mutate(p.name)}>Test</button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {test.isError && <p className="text-sm text-red-400">{(test.error as Error).message}</p>}
    </div>
  );
}
