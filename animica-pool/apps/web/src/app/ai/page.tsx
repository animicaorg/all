"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

const BASE_URL = "https://pool.animica.org/v1";

// Display catalog — pricing mirrors MODEL_PRICING_USD_PER_1K (USD / 1K tokens).
const CATALOG: Record<string, { desc: string; backend: string; in: number; out: number }> = {
  "anm-fast-8b": { desc: "Fast general-purpose chat (8B).", backend: "Animica workers", in: 0.0002, out: 0.0006 },
  "anm-code-7b": { desc: "Code generation & completion (7B).", backend: "Animica workers", in: 0.0003, out: 0.0009 },
  "anm-pro-70b": { desc: "Highest-quality reasoning (70B-class).", backend: "Animica workers / providers", in: 0.0015, out: 0.003 },
  "anm-bittensor-router": { desc: "Routed across the Bittensor subnet.", backend: "Bittensor", in: 0.0008, out: 0.0016 },
  "anm-embed": { desc: "Text embeddings.", backend: "Animica workers", in: 0.0001, out: 0 },
  "anm-worker-small": { desc: "Lightweight worker model (0.5B).", backend: "Animica workers", in: 0.0002, out: 0.0004 },
  "anm-worker-code": { desc: "Worker code model (14B).", backend: "Animica workers", in: 0.0003, out: 0.0006 },
};

interface ModelList { data: { id: string }[] }
interface KeyRow { id: string; prefix: string; label: string | null }

export default function AiPage() {
  const { data: models } = useQuery({ queryKey: ["v1-models"], queryFn: () => api<ModelList>("/v1/models") });
  const { data: keys } = useQuery({ queryKey: ["api-keys"], queryFn: () => api<KeyRow[]>("/api/api-keys"), retry: false });
  const { data: balance } = useQuery({ queryKey: ["balance"], queryFn: () => api<{ balanceUsd: number }>("/api/credits/balance"), retry: false });

  const ids = (models?.data ?? []).map((m) => m.id);
  const live = ids.length > 0;
  const firstKey = keys?.[0]?.prefix;
  const exampleKey = firstKey ? `${firstKey}…` : "anm_live_…";

  const curl = `curl ${BASE_URL}/chat/completions \\
  -H "Authorization: Bearer ${exampleKey}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "anm-fast-8b",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'`;

  const python = `from openai import OpenAI

client = OpenAI(
    base_url="${BASE_URL}",
    api_key="${exampleKey}",
)
resp = client.chat.completions.create(
    model="anm-fast-8b",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)`;

  return (
    <div className="space-y-12">
      <header className="space-y-5">
        <div className="flex flex-wrap items-center gap-3">
          <span className="badge">OpenAI-compatible</span>
          <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs ${live ? "bg-neon-green/15 text-neon-green" : "bg-white/10 text-white/50"}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-neon-green" : "bg-white/40"}`} />
            {live ? "Live" : "Checking…"}
          </span>
        </div>
        <h1 className="text-4xl font-semibold tracking-tightest text-white md:text-6xl">
          AI <span className="grad-text">Inference</span>
        </h1>
        <p className="max-w-2xl text-lg text-white/65">
          A single API with multi-provider routing across Animica workers, Bittensor, and external GPU providers.
          Point any OpenAI SDK at{" "}
          <code className="rounded bg-black/40 px-1.5 py-0.5 text-sm text-neon-blue">{BASE_URL}</code>{" "}
          and pay from your credit balance.
        </p>
        <div className="flex flex-wrap items-center gap-3 pt-1">
          <Link href="/api-keys" className="btn-primary">Get an API key</Link>
          <Link href="/credits" className="btn-ghost">
            Credits{balance ? `: $${balance.balanceUsd.toFixed(2)}` : ""}
          </Link>
        </div>
      </header>

      <section className="space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-neon-green/80">Models</h2>
        <div className="overflow-x-auto rounded-2xl border border-white/10 bg-white/[0.02] backdrop-blur">
          <table className="w-full text-sm">
            <thead className="bg-white/5 text-left text-white/50">
              <tr>
                <th className="px-4 py-3 font-medium">Model</th>
                <th className="px-4 py-3 font-medium">Description</th>
                <th className="px-4 py-3 font-medium">Backend</th>
                <th className="px-4 py-3 text-right font-medium">$/1K in</th>
                <th className="px-4 py-3 text-right font-medium">$/1K out</th>
              </tr>
            </thead>
            <tbody>
              {ids.map((id) => {
                const c = CATALOG[id];
                return (
                  <tr key={id} className="border-t border-white/5 transition-colors hover:bg-white/[0.03]">
                    <td className="px-4 py-3 font-mono text-neon-green">{id}</td>
                    <td className="px-4 py-3 text-white/70">{c?.desc ?? "—"}</td>
                    <td className="px-4 py-3 text-white/50">{c?.backend ?? "—"}</td>
                    <td className="px-4 py-3 text-right text-white/70">{c ? `$${c.in}` : "—"}</td>
                    <td className="px-4 py-3 text-right text-white/70">{c ? `$${c.out}` : "—"}</td>
                  </tr>
                );
              })}
              {ids.length === 0 && (
                <tr><td colSpan={5} className="px-4 py-5 text-white/50">Loading models…</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-neon-green/80">Quickstart</h2>
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-2">
            <p className="text-sm font-medium text-white/80">cURL</p>
            <pre className="overflow-x-auto rounded-2xl border border-white/10 bg-black/40 p-4 text-xs leading-relaxed text-white/80"><code>{curl}</code></pre>
          </div>
          <div className="space-y-2">
            <p className="text-sm font-medium text-white/80">Python</p>
            <pre className="overflow-x-auto rounded-2xl border border-white/10 bg-black/40 p-4 text-xs leading-relaxed text-white/80"><code>{python}</code></pre>
          </div>
        </div>
      </section>

      <Playground models={ids} />
    </div>
  );
}

function Playground({ models }: { models: string[] }) {
  const [model, setModel] = useState(models[0] ?? "anm-fast-8b");
  const [prompt, setPrompt] = useState("Explain what Animica Pool is in one sentence.");
  const [out, setOut] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ msg: string; needLogin?: boolean; needCredits?: boolean } | null>(null);

  const run = async () => {
    setErr(null); setOut(""); setBusy(true);
    try {
      // Session-authed — uses the logged-in user's cookie + credits, no key needed.
      const res = await fetch("/api/ai/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ model, messages: [{ role: "user", content: prompt }] }),
      });
      const text = await res.text();
      const data = text ? JSON.parse(text) : {};
      if (!res.ok) {
        const msg = data?.error?.message || data?.message || res.statusText;
        if (res.status === 401) { setErr({ msg: "Sign in to use the playground.", needLogin: true }); return; }
        if (res.status === 402) { setErr({ msg: "You're out of credits.", needCredits: true }); return; }
        throw new Error(msg);
      }
      setOut(data?.choices?.[0]?.message?.content ?? "(no content)");
    } catch (e) {
      setErr({ msg: String(e instanceof Error ? e.message : e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card space-y-4">
      <div className="space-y-1">
        <h2 className="text-lg font-medium text-white">
          <span className="grad-text">Playground</span>
        </h2>
        <p className="text-sm text-white/55">
          Run a real request against the live cluster — no API key needed while you&apos;re signed in.
          Each run is billed to your credit balance at the rates above.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <select
          value={model} onChange={(e) => setModel(e.target.value)}
          className="field"
        >
          {(models.length ? models : ["anm-fast-8b"]).map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>
      <textarea
        value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3}
        className="field resize-y"
      />
      <button onClick={run} disabled={busy} className="btn-primary">
        {busy ? "Running…" : "Run"}
      </button>
      {err && (
        <p className="text-sm text-red-400">
          {err.msg}{" "}
          {err.needLogin && <Link href="/login" className="text-neon-green hover:underline">Sign in →</Link>}
          {err.needCredits && <Link href="/credits" className="text-neon-green hover:underline">Buy credits →</Link>}
        </p>
      )}
      {out && (
        <div className="rounded-xl border border-white/10 bg-black/30 p-4 text-sm leading-relaxed text-white/85 whitespace-pre-wrap">{out}</div>
      )}
    </section>
  );
}
