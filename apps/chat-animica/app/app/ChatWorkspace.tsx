"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { copyText } from "@/src/shared/clipboard";

type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  ts: number;
  model?: string;
  tokens?: number;
};

type Diagnostics = {
  mode: "strict" | "possibility";
  topK: number;
  validatorStatus: string;
  rewriteCount: number;
  requestId: string;
};

export function ChatWorkspace({ demoMode }: { demoMode: boolean }) {
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [latestSource, setLatestSource] = useState("");
  const [contractId, setContractId] = useState("");
  const [mode, setMode] = useState<"strict" | "possibility">("strict");
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [memoryText, setMemoryText] = useState("");
  const [memoryVersions, setMemoryVersions] = useState<number>(0);
  const [knowledge, setKnowledge] = useState<{ status: string; lastBuiltAt?: string }>({ status: "idle" });
  const [deployStatus, setDeployStatus] = useState("");
  const [txHash, setTxHash] = useState("");
  const [rawTx, setRawTx] = useState("");
  const [receipt, setReceipt] = useState("");
  const [signerType, setSignerType] = useState<"wallet" | "extension" | "dev">("dev");
  const [from, setFrom] = useState("anim1devaddress");

  useEffect(() => {
    fetch("/api/settings").then((r) => r.json()).then((d) => d.mode && setMode(d.mode));
    fetch("/api/projects/memory").then((r) => r.json()).then((d) => {
      setMemoryText(d.latest ?? "");
      setMemoryVersions((d.revisions ?? []).length);
    });
    fetch("/api/knowledge-pack/build").then((r) => r.json()).then(setKnowledge);
  }, []);

  function persist(next: Message[]) {
    setMessages(next.slice(-20));
  }

  async function submit(promptValue: string, strictRequest = false) {
    if (!promptValue.trim()) return;
    setLoading(true);
    setDeployStatus("");

    const userMessage: Message = { id: crypto.randomUUID(), role: "user", text: promptValue, ts: Date.now(), tokens: Math.ceil(promptValue.length / 4) };
    persist([...messages, userMessage]);

    const form = new FormData();
    form.set("prompt", strictRequest ? `Convert to strict contract bundle:\n${promptValue}` : promptValue);
    const res = await fetch("/api/chat", { method: "POST", body: form });
    const data = await res.json();

    if (!res.ok) {
      persist([...messages, userMessage, { id: crypto.randomUUID(), role: "assistant", text: data.error ?? "Failed", ts: Date.now() }]);
      setLoading(false);
      return;
    }

    const assistant: Message = {
      id: crypto.randomUUID(),
      role: "assistant",
      text: data.output?.content ?? "Done",
      ts: Date.now(),
      model: "modal",
      tokens: Math.ceil((data.output?.content?.length ?? 4) / 4)
    };

    setLatestSource(data.output?.content ?? "");
    setContractId(data.contractId ?? "");
    setDiagnostics(data.diagnostics ?? null);
    persist([...messages, userMessage, assistant]);
    setPrompt("");
    setLoading(false);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    await submit(prompt, false);
  }

  async function updateMode(next: "strict" | "possibility") {
    setMode(next);
    await fetch("/api/settings", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ mode: next }) });
  }

  async function saveMemory() {
    const res = await fetch("/api/projects/memory", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ text: memoryText }) });
    const data = await res.json();
    if (res.ok) setMemoryVersions((data.revisions ?? []).length);
  }

  async function buildKnowledgePack() {
    setKnowledge({ status: "building" });
    const res = await fetch("/api/knowledge-pack/build", { method: "POST" });
    const data = await res.json();
    setKnowledge(data);
  }

  async function deployFromArtifact() {
    if (!contractId) return;
    setDeployStatus("Submitting transaction...");
    const res = await fetch("/api/deploy", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ contractId, signerType, from, chainId: 1, nonce: 0, gasLimit: 1200000, fee: 1 })
    });
    const data = await res.json();
    if (!res.ok) {
      setDeployStatus(data.error ?? "Deploy failed");
      return;
    }
    setTxHash(data.txHash ?? "");
    setRawTx(data.rawTx ?? "");
    setReceipt(JSON.stringify(data.receipt ?? {}, null, 2));
    setDeployStatus(`Success (${data.signerMode})`);
  }


  async function sendTestTx() {
    setDeployStatus("Sending RPC format test...");
    const res = await fetch("/api/rpc/test-tx", { method: "POST" });
    const data = await res.json();
    setDeployStatus(res.ok ? "RPC format test succeeded" : `RPC format test failed: ${data.error?.message ?? data.error ?? "unknown"}`);
  }

  async function explainError() {
    if (!rawTx) return;
    const res = await fetch("/api/rpc/explain", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ rawTx }) });
    const data = await res.json();
    alert(res.ok ? JSON.stringify(data.result, null, 2) : data.error);
  }

  const contractBundle = useMemo(() => ({ contractId, source: latestSource, mode }), [contractId, latestSource, mode]);

  return (
    <div className="space-y-3">
      {demoMode ? <div className="card border-amber-500/40 bg-amber-500/10 text-sm">Demo mode active.</div> : null}

      <div className="card flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold">Generation mode</span>
        <button onClick={() => updateMode("strict")} aria-pressed={mode === "strict"} className={`rounded px-3 py-2 ${mode === "strict" ? "bg-indigo-500" : "bg-slate-800"}`}>Strict</button>
        <button onClick={() => updateMode("possibility")} aria-pressed={mode === "possibility"} className={`rounded px-3 py-2 ${mode === "possibility" ? "bg-indigo-500" : "bg-slate-800"}`}>Possibility</button>
        <button onClick={() => setShowDiagnostics((v) => !v)} className="rounded bg-slate-800 px-3 py-2">Diagnostics</button>
      </div>

      {showDiagnostics && diagnostics ? (
        <div className="card text-xs">
          <p>mode: {diagnostics.mode}</p>
          <p>topK chunks: {diagnostics.topK}</p>
          <p>validator status: {diagnostics.validatorStatus}</p>
          <p>rewrite count: {diagnostics.rewriteCount}</p>
          <p>request id: {diagnostics.requestId}</p>
        </div>
      ) : null}

      <form id="chat-composer" onSubmit={onSubmit} className="space-y-2 rounded-xl border border-slate-700 bg-slate-950/95 p-2">
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} aria-label="Describe the Animica contract to generate" rows={3} className="w-full rounded-lg bg-slate-800 p-3 text-sm" />
        <div className="grid gap-2 sm:grid-cols-2">
          <button disabled={loading} className="rounded-lg bg-indigo-500 px-4 py-3 text-sm font-semibold disabled:opacity-60">{loading ? "Working..." : "Generate Contract"}</button>
          <button type="button" disabled={loading} onClick={() => submit(prompt, true)} className="rounded-lg bg-slate-700 px-4 py-3 text-sm">Convert idea to strict contract</button>
        </div>
      </form>

      <div className="space-y-2">
        {messages.map((m) => (
          <article key={m.id} className={`rounded-xl border p-3 text-sm ${m.role === "assistant" ? "border-slate-700 bg-slate-900" : "border-indigo-400/40 bg-indigo-500/10"}`}>
            <p className="whitespace-pre-wrap">{m.text}</p>
          </article>
        ))}
        {loading ? <div className="animate-pulse rounded-xl border border-slate-700 p-4 text-sm text-slate-400">LLM request in progress…</div> : null}
      </div>

      <div className="card space-y-2 text-xs">
        <h3 className="font-semibold">Project memory (server persisted)</h3>
        <textarea value={memoryText} onChange={(e) => setMemoryText(e.target.value)} rows={3} className="w-full rounded bg-slate-800 p-2" />
        <div className="flex items-center justify-between">
          <span>Versions kept: {memoryVersions}/10</span>
          <button onClick={saveMemory} className="rounded bg-indigo-500 px-2 py-1">Save Project Memory</button>
        </div>
      </div>

      <div className="card flex items-center justify-between text-xs">
        <div>
          <p className="font-semibold">Knowledge Pack</p>
          <p>Status: {knowledge.status}</p>
          {knowledge.lastBuiltAt ? <p>Last build: {new Date(knowledge.lastBuiltAt).toLocaleString()}</p> : null}
        </div>
        <button onClick={buildKnowledgePack} className="rounded bg-indigo-500 px-2 py-1">Build Knowledge Pack</button>
      </div>

      <div className="card space-y-2 text-xs">
        <h3 className="font-semibold">Contract Bundle</h3>
        <pre className="max-h-32 overflow-auto rounded bg-slate-950 p-2">{JSON.stringify(contractBundle, null, 2)}</pre>
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="space-y-1">Signer
            <select value={signerType} onChange={(e) => setSignerType(e.target.value as any)} className="w-full rounded bg-slate-800 p-2">
              <option value="wallet">Wallet</option>
              <option value="extension">Extension</option>
              <option value="dev">Local key signer</option>
            </select>
          </label>
          <label className="space-y-1">From address
            <input value={from} onChange={(e) => setFrom(e.target.value)} className="w-full rounded bg-slate-800 p-2" />
          </label>
        </div>
        <div className="grid gap-2 sm:grid-cols-2"><button onClick={deployFromArtifact} disabled={!contractId} className="w-full rounded bg-indigo-500 px-3 py-2 disabled:opacity-50">Deploy</button><button onClick={sendTestTx} className="w-full rounded bg-slate-700 px-3 py-2">Send Test Tx</button></div>
        <p>{deployStatus}</p>
        {txHash ? <div className="space-y-1"><p>tx hash: {txHash}</p><div className="flex gap-2"><button className="rounded bg-slate-800 px-2 py-1" onClick={() => copyText(txHash)}>Copy hash</button><a href={`/app/deploys?tx=${txHash}`} className="rounded bg-slate-800 px-2 py-1">Open explorer view</a></div></div> : null}
        {rawTx ? <div><button className="rounded bg-slate-800 px-2 py-1" onClick={() => copyText(rawTx)}>Copy rawTx</button> <button className="rounded bg-slate-800 px-2 py-1" onClick={explainError}>Explain error</button></div> : null}
        {receipt ? <div><button className="rounded bg-slate-800 px-2 py-1" onClick={() => copyText(receipt)}>Copy receipt</button><pre className="max-h-28 overflow-auto rounded bg-slate-950 p-2">{receipt}</pre></div> : null}
      </div>
    </div>
  );
}
