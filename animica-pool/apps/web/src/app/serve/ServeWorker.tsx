"use client";

/**
 * Serve & Earn — a browser-native AICF inference worker.
 *
 * ONE core loop (model load → register → claim → generate → submit) is shipped as a
 * self-contained module string and run in a DEDICATED WEB WORKER, so serving keeps
 * going when the tab is backgrounded — main-thread timers are throttled/frozen in
 * hidden tabs, worker threads are not. Where WebGPU isn't exposed to workers
 * (Safari), the exact same module runs on the main thread instead (foreground only).
 * An opt-in silent-audio keepalive additionally stops Android Chrome from freezing
 * the whole tab a few minutes after it is backgrounded.
 *
 * Protocol (JSON-RPC 2.0, POST https://rpc.animica.org/rpc — CORS is open):
 *   aicf.workerRegister     {address, tiers, hardware}
 *   aicf.workerClaimNextJob {address, tiers} -> null | {job_id, prompt,
 *                            max_output_tokens, temperature, top_p, claim_expires_at}
 *   aicf.workerSubmitResult {address, job_id, text} -> {accepted, reason?}
 *   aicf.workerEarnings     {address} -> {jobs_completed, earnings_pending_animica}
 *
 * Live-verified 2026-08-23: this exact loop registered, claimed a real job from the
 * shared queue, its answer was served by animica.dev/v1, and the address was credited
 * (aicf.workerEarnings). Jobs are K-way raced server-side; losing to a faster desktop
 * GPU is normal and shown honestly. No keys ever touch this page — the address is
 * only where earnings are credited.
 */

import { useCallback, useEffect, useRef, useState } from "react";

const RPC_URL = "https://rpc.animica.org/rpc";
const WEBLLM_URL = "https://esm.run/@mlc-ai/web-llm@0.2.79";
const TIERS = ["free", "standard"];
const MODELS: { id: string; label: string; note: string }[] = [
  { id: "Qwen2.5-1.5B-Instruct-q4f16_1-MLC", label: "Qwen 2.5 · 1.5B (default)", note: "~1.0 GB · best answers, needs ~3 GB free RAM" },
  { id: "Qwen2.5-0.5B-Instruct-q4f16_1-MLC", label: "Qwen 2.5 · 0.5B (light)", note: "~0.5 GB · for older / low-RAM phones" },
  { id: "Llama-3.2-1B-Instruct-q4f16_1-MLC", label: "Llama 3.2 · 1B", note: "~0.8 GB · alternative to Qwen" },
];

type Phase = "idle" | "loading" | "serving" | "paused" | "stopped" | "error";

interface Stats {
  won: number;
  lost: number;
  tokensOut: number;
  lastTokS: number | null;
  pendingANM: number | null;
  jobsCompleted: number | null;
}

// ── The worker core: a complete module, identical in both harnesses ──────────
// Plain quotes only (this is a template literal); no JSX, no bundler imports.
const CORE_SOURCE = `
const post = (m) => __POST__(m);
let stopped = false;
let paused = false;
let engine = null;
export function __control(msg) {
  if (msg === "stop") { stopped = true; try { engine && engine.interruptGenerate && engine.interruptGenerate(); } catch (e) {} }
  if (msg === "pause") { paused = true; }
  if (msg === "resume") { paused = false; }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function rpc(url, method, params) {
  const res = await fetch(url, { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: (Date.now() % 1e9) + Math.floor(Math.random() * 1e3), method, params }) });
  if (!res.ok) throw new Error(method + ": HTTP " + res.status);
  const j = await res.json();
  if (j.error) throw new Error(method + ": " + (j.error.message || "rpc error"));
  return j.result;
}
// The bridge flattens system preamble + history + question into one prompt; real ones
// measure ~10 KB. The MLC prebuilds carry a 4k-token context, so clamp defensively:
// keep the head (instructions) and the tail (recent history + the actual question).
function clampPrompt(p, maxChars) {
  if (p.length <= maxChars) return p;
  const head = Math.floor(maxChars * 0.3);
  const tail = maxChars - head;
  return p.slice(0, head) + "\\n…\\n" + p.slice(p.length - tail);
}
export async function run(cfg) {
  try {
    post({ type: "status", text: "Loading WebLLM…" });
    const webllm = await import(cfg.webllmUrl);
    if (stopped) return;
    post({ type: "status", text: "Downloading the model (cached after the first time)…" });
    engine = await webllm.CreateMLCEngine(cfg.modelId, {
      initProgressCallback: (p) => {
        if (stopped) return;
        post({ type: "progress", pct: typeof p.progress === "number" ? Math.round(p.progress * 100) : null,
               text: p.text ? String(p.text).slice(0, 90) : null });
      },
    });
    if (stopped) { try { engine.unload && engine.unload(); } catch (e) {} return; }
    post({ type: "ready" });
    post({ type: "log", text: "model ready: " + cfg.modelId });
    await rpc(cfg.rpcUrl, "aicf.workerRegister", { address: cfg.address, tiers: cfg.tiers, hardware: cfg.hardware });
    post({ type: "log", text: "registered " + cfg.address.slice(0, 14) + "… tiers=" + cfg.tiers.join(",") });
    post({ type: "status", text: "Serving — waiting for jobs…" });

    let delay = 2500;
    let lastRegister = Date.now();
    let lastEarnings = 0;
    while (!stopped) {
      if (paused) { await sleep(1200); continue; }
      if (Date.now() - lastRegister > 300000) {
        lastRegister = Date.now();
        rpc(cfg.rpcUrl, "aicf.workerRegister", { address: cfg.address, tiers: cfg.tiers, hardware: cfg.hardware }).catch(() => {});
      }
      if (Date.now() - lastEarnings > 45000) {
        lastEarnings = Date.now();
        rpc(cfg.rpcUrl, "aicf.workerEarnings", { address: cfg.address })
          .then((e) => post({ type: "earnings", pending: Number(e && e.earnings_pending_animica || 0), completed: Number(e && e.jobs_completed || 0) }))
          .catch(() => {});
      }
      let job = null;
      try {
        job = await rpc(cfg.rpcUrl, "aicf.workerClaimNextJob", { address: cfg.address, tiers: cfg.tiers });
      } catch (e) {
        post({ type: "status", text: "Queue unreachable (" + String(e && e.message || e).slice(0, 60) + ") — retrying…" });
        delay = Math.min(15000, delay * 1.6);
        await sleep(delay);
        continue;
      }
      if (!job || !job.job_id) {
        delay = Math.min(15000, delay * 1.35);
        await sleep(delay * (0.7 + Math.random() * 0.6));
        continue;
      }
      delay = 2500;
      const prompt = clampPrompt(String(job.prompt || ""), 13000);
      if (!prompt.trim()) { post({ type: "log", text: "claimed " + job.job_id.slice(0, 10) + "… but it carried no prompt — skipped" }); continue; }
      const maxTok = Math.max(16, Math.min(Number(job.max_output_tokens) || 512, cfg.maxOutputCap));
      const deadline = Number(job.claim_expires_at) > 0 ? Number(job.claim_expires_at) * 1000 : Date.now() + 120000;
      post({ type: "status", text: "Answering job " + job.job_id.slice(0, 10) + "… (" + prompt.length + " chars in, ≤" + maxTok + " tokens out)" });
      post({ type: "log", text: "claimed " + job.job_id.slice(0, 10) + "… tier=" + job.tier });
      let text = "";
      let tokens = 0;
      const t0 = Date.now();
      const watchdog = setTimeout(() => { try { engine.interruptGenerate && engine.interruptGenerate(); } catch (e) {} },
        Math.max(5000, deadline - Date.now() - 4000));
      try {
        const chunks = await engine.chat.completions.create({
          messages: [{ role: "user", content: prompt }],
          max_tokens: maxTok,
          temperature: Math.max(0, Math.min(Number(job.temperature != null ? job.temperature : 0.3), 1.2)),
          top_p: Math.max(0.05, Math.min(Number(job.top_p != null ? job.top_p : 0.9), 1)),
          stream: true,
        });
        for await (const c of chunks) {
          if (stopped) break;
          const piece = (c && c.choices && c.choices[0] && c.choices[0].delta && c.choices[0].delta.content) || "";
          if (piece) { text += piece; tokens += 1; }
        }
      } catch (e) {
        post({ type: "log", text: "generation failed: " + String(e && e.message || e).slice(0, 80) });
      } finally {
        clearTimeout(watchdog);
      }
      const dt = (Date.now() - t0) / 1000;
      if (stopped) break;
      if (!text.trim()) { post({ type: "log", text: "no text produced for " + job.job_id.slice(0, 10) + "… — nothing submitted" }); continue; }
      try {
        const r = await rpc(cfg.rpcUrl, "aicf.workerSubmitResult", { address: cfg.address, job_id: job.job_id, text: text.slice(0, 32000) });
        const tokS = tokens > 0 && dt > 0 ? tokens / dt : null;
        if (r && r.accepted !== false) {
          post({ type: "job", won: true, tokens: tokens, tokS: tokS });
          post({ type: "log", text: "WON " + job.job_id.slice(0, 10) + "… · " + tokens + " tok in " + dt.toFixed(1) + "s" + (tokS ? " (" + tokS.toFixed(1) + " tok/s)" : "") });
        } else {
          post({ type: "job", won: false, tokens: tokens, tokS: tokS });
          post({ type: "log", text: "lost the race on " + job.job_id.slice(0, 10) + "… (" + ((r && r.reason) || "another worker was faster") + ")" });
        }
      } catch (e) {
        post({ type: "log", text: "submit failed: " + String(e && e.message || e).slice(0, 80) });
      }
      post({ type: "status", text: "Serving — waiting for jobs…" });
    }
  } catch (e) {
    post({ type: "fatal", text: String(e && e.message || e).slice(0, 200) });
    return;
  } finally {
    try { engine && engine.unload && engine.unload(); } catch (e) {}
  }
  post({ type: "stopped" });
}
`;

const WORKER_HARNESS = `
${CORE_SOURCE.replace("__POST__(m)", "self.postMessage(m)")}
self.onmessage = (ev) => {
  const d = ev.data || {};
  if (d.type === "start") run(d.cfg);
  else if (d.type === "control") __control(d.cmd);
};
self.postMessage({ type: "boot", webgpu: !!(self.navigator && self.navigator.gpu) });
`;

function makeInlineModule(): string {
  // Same core, main-thread flavor: post() calls a global the component registers.
  return CORE_SOURCE.replace("__POST__(m)", "(globalThis.__anmServePost || (() => {}))(m)");
}

function looksLikeAnimAddress(a: string): boolean {
  return /^anim1[023456789acdefghjklmnpqrstuvwxyz]{20,}$/.test(a.trim());
}

function fmtANM(v: number | null): string {
  if (v == null) return "—";
  return v >= 1 ? v.toFixed(3) : v.toFixed(6);
}

export default function ServeWorker() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [status, setStatus] = useState<string>("");
  const [progress, setProgress] = useState<number | null>(null);
  const [address, setAddress] = useState<string>("");
  const [modelId, setModelId] = useState<string>(MODELS[0].id);
  const [chargeOnly, setChargeOnly] = useState<boolean>(true);
  const [bgMode, setBgMode] = useState<boolean>(true);
  const [batterySupported, setBatterySupported] = useState<boolean>(false);
  const [charging, setCharging] = useState<boolean>(true);
  const [gpuOk, setGpuOk] = useState<boolean | null>(null);
  const [mode, setMode] = useState<"worker" | "inline" | null>(null);
  const [stats, setStats] = useState<Stats>({ won: 0, lost: 0, tokensOut: 0, lastTokS: null, pendingANM: null, jobsCompleted: null });
  const [log, setLog] = useState<string[]>([]);

  const workerRef = useRef<Worker | null>(null);
  const inlineRef = useRef<any>(null);           // inline module (Safari fallback)
  const runRef = useRef<number>(0);
  const wakeLockRef = useRef<any>(null);
  const audioRef = useRef<{ ctx: AudioContext; osc: OscillatorNode } | null>(null);
  const phaseRef = useRef<Phase>("idle");
  useEffect(() => { phaseRef.current = phase; }, [phase]);

  const addLog = useCallback((line: string) => {
    const t = new Date().toLocaleTimeString();
    setLog((l) => [`${t}  ${line}`, ...l].slice(0, 60));
  }, []);

  // ── environment probes + persisted prefs ──────────────────────────────────
  useEffect(() => {
    setGpuOk(typeof navigator !== "undefined" && !!(navigator as any).gpu);
    try {
      const a = localStorage.getItem("anmServeAddress"); if (a) setAddress(a);
      const m = localStorage.getItem("anmServeModel"); if (m && MODELS.some((x) => x.id === m)) setModelId(m);
      const c = localStorage.getItem("anmServeChargeOnly"); if (c != null) setChargeOnly(c !== "0");
      const b = localStorage.getItem("anmServeBgMode"); if (b != null) setBgMode(b !== "0");
    } catch { /* private mode */ }
    const nav = navigator as any;
    if (typeof nav.getBattery === "function") {
      nav.getBattery().then((b: any) => {
        setBatterySupported(true);
        setCharging(!!b.charging);
        b.addEventListener("chargingchange", () => setCharging(!!b.charging));
      }).catch(() => setBatterySupported(false));
    }
  }, []);
  useEffect(() => { try { localStorage.setItem("anmServeAddress", address); } catch { /* */ } }, [address]);
  useEffect(() => { try { localStorage.setItem("anmServeModel", modelId); } catch { /* */ } }, [modelId]);
  useEffect(() => { try { localStorage.setItem("anmServeChargeOnly", chargeOnly ? "1" : "0"); } catch { /* */ } }, [chargeOnly]);
  useEffect(() => { try { localStorage.setItem("anmServeBgMode", bgMode ? "1" : "0"); } catch { /* */ } }, [bgMode]);

  // ── charging gate → pause/resume the core ─────────────────────────────────
  const sendControl = useCallback((cmd: "pause" | "resume" | "stop") => {
    workerRef.current?.postMessage({ type: "control", cmd });
    try { inlineRef.current?.__control?.(cmd); } catch { /* */ }
  }, []);
  useEffect(() => {
    if (phase !== "serving" && phase !== "paused") return;
    const block = chargeOnly && batterySupported && !charging;
    if (block && phase === "serving") {
      sendControl("pause"); setPhase("paused"); setStatus("Paused — plug the phone in to keep serving.");
    } else if (!block && phase === "paused") {
      sendControl("resume"); setPhase("serving"); setStatus("Serving — waiting for jobs…");
    }
  }, [charging, chargeOnly, batterySupported, phase, sendControl]);

  async function acquireWakeLock() {
    try { const wl = (navigator as any).wakeLock; if (wl?.request) wakeLockRef.current = await wl.request("screen"); } catch { /* */ }
  }
  function releaseWakeLock() { try { wakeLockRef.current?.release?.(); } catch { /* */ } wakeLockRef.current = null; }
  useEffect(() => {
    // Re-grab the wake lock when the tab comes back (it auto-releases on hide).
    const onVis = () => { if (document.visibilityState === "visible" && (phaseRef.current === "serving" || phaseRef.current === "loading")) acquireWakeLock(); };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  function startKeepalive() {
    // A near-silent tone marks the tab as "playing audio", which exempts it from
    // Android Chrome's background tab freezing. Started from the click handler so
    // autoplay policy allows it. ~0 battery cost next to running an LLM.
    try {
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      gain.gain.value = 0.0004;
      osc.frequency.value = 30;
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      void ctx.resume();
      audioRef.current = { ctx, osc };
    } catch { /* keepalive is best-effort */ }
  }
  function stopKeepalive() {
    try { audioRef.current?.osc.stop(); void audioRef.current?.ctx.close(); } catch { /* */ }
    audioRef.current = null;
  }

  const onMessage = useCallback((m: any) => {
    if (!m || typeof m !== "object") return;
    switch (m.type) {
      case "status": setStatus(m.text || ""); break;
      case "progress":
        setProgress(m.pct ?? null);
        if (m.text) setStatus(m.text + "…");
        break;
      case "ready": setProgress(null); setPhase("serving"); break;
      case "log": addLog(m.text || ""); break;
      case "job":
        setStats((s) => ({
          ...s,
          won: s.won + (m.won ? 1 : 0),
          lost: s.lost + (m.won ? 0 : 1),
          tokensOut: s.tokensOut + (Number(m.tokens) || 0),
          lastTokS: m.tokS ?? s.lastTokS,
        }));
        break;
      case "earnings": setStats((s) => ({ ...s, pendingANM: m.pending, jobsCompleted: m.completed })); break;
      case "fatal": setPhase("error"); setStatus(`Stopped: ${m.text}`); cleanup(); break;
      case "stopped": if (phaseRef.current !== "error") { setPhase("stopped"); } cleanup(); break;
      default: break;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addLog]);

  function cleanup() {
    workerRef.current?.terminate();
    workerRef.current = null;
    inlineRef.current = null;
    (globalThis as any).__anmServePost = undefined;
    releaseWakeLock();
    stopKeepalive();
  }

  // ── start / stop ──────────────────────────────────────────────────────────
  const start = useCallback(async () => {
    if (!address.trim()) { setStatus("Enter the anim1… address that should be paid."); return; }
    if (!looksLikeAnimAddress(address) && !window.confirm(
      "That doesn't look like an anim1… wallet address. Earnings credit whatever ID you register — an unknown ID can never be paid out. Continue anyway?")) return;
    runRef.current += 1;
    setPhase("loading");
    setStatus("Starting…");
    setProgress(0);
    await acquireWakeLock();
    if (bgMode) startKeepalive();

    const cfg = {
      address: address.trim(),
      modelId,
      tiers: TIERS,
      rpcUrl: RPC_URL,
      webllmUrl: WEBLLM_URL,
      maxOutputCap: 768,
      hardware: {
        engine: "webllm",
        model: modelId,
        ua: navigator.userAgent.slice(0, 160),
        platform: (navigator as any).userAgentData?.platform || navigator.platform || "",
        cores: navigator.hardwareConcurrency || 0,
        device_memory_gb: (navigator as any).deviceMemory || 0,
      },
    };

    // Preferred: dedicated worker (keeps serving when the tab is backgrounded).
    // Fallback: same module inline when the worker has no WebGPU (Safari).
    try {
      const blob = new Blob([WORKER_HARNESS], { type: "text/javascript" });
      const w = new Worker(URL.createObjectURL(blob), { type: "module" });
      const booted: boolean = await new Promise((resolve) => {
        const t = setTimeout(() => resolve(false), 4000);
        w.onmessage = (ev) => {
          if (ev.data?.type === "boot") { clearTimeout(t); resolve(!!ev.data.webgpu); }
        };
        w.onerror = () => { clearTimeout(t); resolve(false); };
      });
      if (booted) {
        workerRef.current = w;
        setMode("worker");
        w.onmessage = (ev) => onMessage(ev.data);
        w.onerror = (e) => { setPhase("error"); setStatus(`Worker error: ${e.message || e}`); cleanup(); };
        w.postMessage({ type: "start", cfg });
        addLog("running in a background worker — serving continues while the tab is hidden");
        return;
      }
      w.terminate();
    } catch { /* fall through to inline */ }

    try {
      setMode("inline");
      addLog("this browser has no WebGPU in workers — running in the page (keep this tab in the foreground)");
      (globalThis as any).__anmServePost = onMessage;
      const blob = new Blob([makeInlineModule()], { type: "text/javascript" });
      const mod: any = await import(/* webpackIgnore: true */ URL.createObjectURL(blob));
      inlineRef.current = mod;
      void mod.run(cfg);
    } catch (e: any) {
      setPhase("error");
      setStatus(`Couldn't start: ${String(e?.message || e).slice(0, 160)}`);
      cleanup();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [address, modelId, bgMode, onMessage]);

  const stop = useCallback(() => {
    sendControl("stop");
    // give the core a moment to unload the engine, then hard-terminate
    window.setTimeout(() => cleanup(), 2500);
    setPhase("stopped");
    setStatus("Stopped. Your pending earnings stay on your address.");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sendControl]);

  useEffect(() => () => { sendControl("stop"); cleanup(); }, []); // unmount

  const running = phase === "loading" || phase === "serving" || phase === "paused";

  return (
    <div className="space-y-4 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
      {gpuOk === false && (
        <div className="rounded-lg border border-amber-400/40 bg-amber-400/10 p-3 text-sm text-amber-200">
          This browser has no WebGPU, so the model can&apos;t run here. Use Chrome/Edge on Android or
          desktop; on iPhone use Safari on iOS&nbsp;18+ (enable WebGPU under Settings → Safari →
          Advanced → Feature Flags if it reports unavailable).
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        <label className="block text-sm text-white/70">
          Payout address (anim1…)
          <input
            value={address}
            onChange={(e) => setAddress(e.target.value.trim())}
            disabled={running}
            placeholder="anim1…  (create one: animica.org/wallet)"
            className="mt-1 w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 font-mono text-sm text-white placeholder-white/30 outline-none focus:border-neon-green/60"
          />
        </label>
        <label className="block text-sm text-white/70">
          Model
          <select
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            disabled={running}
            className="mt-1 w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-sm text-white outline-none focus:border-neon-green/60"
          >
            {MODELS.map((m) => (
              <option key={m.id} value={m.id}>{m.label} — {m.note}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        {!running ? (
          <button
            onClick={start}
            disabled={gpuOk === false}
            className="rounded-xl bg-neon-green px-6 py-2.5 font-semibold text-black transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Start serving
          </button>
        ) : (
          <button onClick={stop} className="rounded-xl border border-white/25 px-6 py-2.5 font-semibold text-white transition hover:bg-white/10">
            Stop
          </button>
        )}
        <label className="flex items-center gap-2 text-sm text-white/70">
          <input type="checkbox" checked={chargeOnly} onChange={(e) => setChargeOnly(e.target.checked)} className="h-4 w-4 accent-[#14C79B]" />
          Only while charging{batterySupported ? "" : " (battery state not exposed here — manual)"}
        </label>
        <label className="flex items-center gap-2 text-sm text-white/70" title="Plays a near-silent tone so Android Chrome doesn't freeze the tab when you switch apps. iOS suspends background tabs regardless — keep the tab open there.">
          <input type="checkbox" checked={bgMode} disabled={running} onChange={(e) => setBgMode(e.target.checked)} className="h-4 w-4 accent-[#14C79B]" />
          Keep serving in background
        </label>
      </div>

      <div className="rounded-lg border border-white/10 bg-black/30 p-3 font-mono text-sm text-white/80">
        <span className={
          phase === "serving" ? "text-neon-green" : phase === "paused" ? "text-amber-300" : phase === "error" ? "text-red-400" : "text-white/60"
        }>
          {phase === "idle" ? "idle — enter an address and press Start" : status || phase}
        </span>
        {phase === "serving" && mode === "worker" && (
          <span className="ml-2 text-xs text-white/40">· background-capable worker</span>
        )}
        {progress != null && (
          <div className="mt-2 h-1.5 w-full overflow-hidden rounded bg-white/10">
            <div className="h-full bg-neon-green transition-all" style={{ width: `${progress}%` }} />
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 text-center md:grid-cols-5">
        <StatBox label="jobs won" value={String(stats.won)} accent />
        <StatBox label="races lost" value={String(stats.lost)} />
        <StatBox label="tokens out" value={stats.tokensOut.toLocaleString()} />
        <StatBox label="speed" value={stats.lastTokS ? `${stats.lastTokS.toFixed(1)} tok/s` : "—"} />
        <StatBox label="pending ANM" value={fmtANM(stats.pendingANM)} accent />
      </div>
      {stats.jobsCompleted != null && (
        <p className="text-xs text-white/40">
          Ledger for this address: {stats.jobsCompleted} jobs completed all-time · pending earnings are
          IOUs on the node&apos;s worker ledger and settle on-chain via the AICF carve.
        </p>
      )}

      {log.length > 0 && (
        <details className="text-xs text-white/50" open>
          <summary className="cursor-pointer select-none text-white/60">activity</summary>
          <div className="mt-2 max-h-44 space-y-1 overflow-y-auto font-mono">
            {log.map((l, i) => <div key={i}>{l}</div>)}
          </div>
        </details>
      )}
    </div>
  );
}

function StatBox({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.03] px-2 py-3">
      <div className={`text-lg font-semibold ${accent ? "text-neon-green" : "text-white"}`}>{value}</div>
      <div className="mt-0.5 text-[11px] uppercase tracking-wider text-white/40">{label}</div>
    </div>
  );
}
