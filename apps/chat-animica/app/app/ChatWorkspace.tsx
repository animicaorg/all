"use client";

import { FormEvent, useMemo, useState } from "react";

type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  ts: number;
  model?: string;
  tokens?: number;
  deployed?: boolean;
};

const QUICK_ACTIONS = ["Generate Contract", "Explain Error", "Simulate", "Deploy"];

export function ChatWorkspace({ demoMode }: { demoMode: boolean }) {
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState<Message[]>(() => {
    if (typeof window === "undefined") return [];
    const raw = localStorage.getItem("animica-chat-cache");
    return raw ? JSON.parse(raw) : [];
  });
  const [loading, setLoading] = useState(false);
  const [artifactOpen, setArtifactOpen] = useState(false);
  const [latestSource, setLatestSource] = useState("");
  const [contractId, setContractId] = useState("");
  const [signerType, setSignerType] = useState<"extension" | "wallet" | "dev">("wallet");
  const [deployStatus, setDeployStatus] = useState<string>("idle");

  const limited = demoMode && messages.filter((m) => m.role === "user").length >= 5;

  function persist(next: Message[]) {
    const slice = next.slice(-20);
    setMessages(slice);
    localStorage.setItem("animica-chat-cache", JSON.stringify(slice));
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!prompt.trim() || limited) return;

    const userMessage: Message = { id: crypto.randomUUID(), role: "user", text: prompt, ts: Date.now(), tokens: Math.ceil(prompt.length / 4) };
    const optimistic: Message = {
      id: crypto.randomUUID(),
      role: "assistant",
      text: "Generating contract...",
      ts: Date.now(),
      model: "modal-gpt",
      tokens: 0
    };

    persist([...messages, userMessage, optimistic]);
    setLoading(true);

    const form = new FormData();
    form.set("prompt", prompt);
    const res = await fetch("/api/chat", { method: "POST", body: form });
    const data = await res.json();

    if (!res.ok) {
      persist([...messages, userMessage, { ...optimistic, text: data.error ?? "Failed", deployed: false }]);
      navigator.vibrate?.([10, 20, 10]);
      setLoading(false);
      return;
    }

    const assistant: Message = {
      id: crypto.randomUUID(),
      role: "assistant",
      text: data.output?.content ?? "Done",
      ts: Date.now(),
      model: "modal-gpt",
      tokens: Math.ceil((data.output?.content?.length ?? 4) / 4),
      deployed: false
    };
    setLatestSource(data.output?.content ?? "");
    setContractId(data.contractId ?? "");
    persist([...messages, userMessage, assistant]);
    setPrompt("");
    setLoading(false);
    navigator.vibrate?.(20);
  }


  async function deployFromArtifact() {
    if (!contractId) return;
    setDeployStatus("submitting");
    const payload = { contractId, signerType, txDraft: { contractId, sourceLength: latestSource.length, ts: Date.now() } };
    const res = await fetch("/api/deploy", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) });
    const data = await res.json();
    if (!res.ok) {
      setDeployStatus(data.error ?? "failed");
      navigator.vibrate?.([10, 30, 10]);
      return;
    }
    setDeployStatus(`queued:${data.deployId}`);
    navigator.vibrate?.(40);
  }
  const onboardingStep = useMemo(() => {
    if (!messages.length) return 1;
    if (messages.length < 3) return 2;
    if (messages.length < 5) return 3;
    return 4;
  }, [messages.length]);

  return (
    <div className="space-y-3">
      {demoMode ? <div className="card border-amber-500/40 bg-amber-500/10 text-sm">Demo Mode: 5 chat messages allowed. Deploy remains locked until subscription is active.</div> : null}
      <div className="card text-sm">
        <p className="font-semibold">60-second onboarding</p>
        <ol className="mt-2 list-decimal space-y-1 pl-4 text-slate-300">
          <li className={onboardingStep >= 1 ? "text-indigo-200" : ""}>Connect wallet</li>
          <li className={onboardingStep >= 2 ? "text-indigo-200" : ""}>Generate first contract template</li>
          <li className={onboardingStep >= 3 ? "text-indigo-200" : ""}>Simulate</li>
          <li className={onboardingStep >= 4 ? "text-indigo-200" : ""}>Deploy</li>
        </ol>
      </div>

      <div className="flex gap-2 overflow-x-auto pb-1">
        {QUICK_ACTIONS.map((action) => (
          <button key={action} onClick={() => setPrompt((prev) => `${action}: ${prev}`.trim())} className="shrink-0 rounded-full border border-slate-700 px-3 py-2 text-xs">
            {action}
          </button>
        ))}
      </div>

      <div className="space-y-2">
        {messages.map((message) => (
          <article key={message.id} className={`rounded-xl border p-3 text-sm ${message.role === "assistant" ? "border-slate-700 bg-slate-900" : "border-indigo-400/40 bg-indigo-500/10"}`}>
            <p className="whitespace-pre-wrap">{message.text}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
              <span className="rounded bg-slate-800 px-2 py-1">{message.model ?? "user"}</span>
              <span className="rounded bg-slate-800 px-2 py-1">~{message.tokens ?? 0} tokens</span>
              <span className="rounded bg-slate-800 px-2 py-1">{new Date(message.ts).toLocaleTimeString()}</span>
              {message.deployed ? <span className="rounded bg-emerald-500/30 px-2 py-1 text-emerald-200">Deployed</span> : null}
            </div>
            <div className="mt-2 flex gap-2 text-xs">
              <button onClick={() => navigator.clipboard.writeText(message.text)} className="rounded bg-slate-800 px-2 py-1">Copy</button>
              <button onClick={() => setPrompt(message.text)} className="rounded bg-slate-800 px-2 py-1">Regenerate</button>
              <button onClick={() => localStorage.setItem("animica-artifact", message.text)} className="rounded bg-slate-800 px-2 py-1">Save artifact</button>
            </div>
          </article>
        ))}
        {loading ? <div className="animate-pulse rounded-xl border border-slate-700 p-4 text-sm text-slate-400">Thinking…</div> : null}
      </div>

      <form id="chat-composer" onSubmit={onSubmit} className="sticky bottom-16 z-10 space-y-2 rounded-xl border border-slate-700 bg-slate-950/95 p-2 backdrop-blur md:bottom-4">
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Describe the Animica contract to generate" rows={3} className="w-full rounded-lg bg-slate-800 p-3 text-sm" />
        <button disabled={loading || limited} className="w-full rounded-lg bg-indigo-500 px-4 py-3 text-sm font-semibold disabled:opacity-60">{limited ? "Demo limit reached" : "Generate Contract"}</button>
      </form>

      <button onClick={() => setArtifactOpen(true)} className="w-full rounded-lg border border-slate-700 px-3 py-2 text-sm">Open Contract Artifact Panel</button>
      {artifactOpen ? (
        <div className="fixed inset-x-0 bottom-0 z-30 rounded-t-2xl border border-slate-700 bg-slate-900 p-4">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="font-semibold">Contract Artifact Panel</h3>
            <button onClick={() => setArtifactOpen(false)}>Close</button>
          </div>
          <pre className="max-h-32 overflow-auto rounded bg-slate-950 p-2 text-xs">{latestSource || "No source yet"}</pre>
          <div className="mt-3 space-y-2">
            <div className="grid grid-cols-3 gap-2">
              <button className="rounded bg-slate-800 px-2 py-2 text-xs">Compile</button>
              <button className="rounded bg-slate-800 px-2 py-2 text-xs">Simulate</button>
              <button onClick={deployFromArtifact} disabled={demoMode || !contractId} className="rounded bg-indigo-500 px-2 py-2 text-xs disabled:opacity-50">Deploy</button>
            </div>
            <label className="text-xs text-slate-300">Signer
              <select value={signerType} onChange={(e) => setSignerType(e.target.value as any)} className="mt-1 w-full rounded bg-slate-800 p-2 text-xs">
                <option value="wallet">Animica Wallet (Mobile)</option>
                <option value="extension">Browser Extension</option>
                <option value="dev">Dev Signer</option>
              </select>
            </label>
            <p className="text-xs text-slate-400">Deploy status: {deployStatus}</p>
          </div>
        </div>
      ) : null}
    </div>
  );
}
