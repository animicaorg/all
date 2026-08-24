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
// CPU (WebAssembly) fallback engine for browsers WITHOUT WebGPU — iPhone Safari first
// among them. llama.cpp compiled to wasm; single-thread build needs no COOP/COEP
// headers, so it runs on this page as-is. Slower, honest, works everywhere.
const WLLAMA_URL = "https://cdn.jsdelivr.net/npm/@wllama/wllama@2.3.5/esm/index.js";
const WLLAMA_WASM = {
  "single-thread/wllama.wasm": "https://cdn.jsdelivr.net/npm/@wllama/wllama@2.3.5/esm/single-thread/wllama.wasm",
  "multi-thread/wllama.wasm": "https://cdn.jsdelivr.net/npm/@wllama/wllama@2.3.5/esm/multi-thread/wllama.wasm",
};
const TIERS = ["free", "standard"];
const MODELS: { id: string; label: string; note: string; approxGB: number; gguf: string }[] = [
  { id: "Qwen2.5-1.5B-Instruct-q4f16_1-MLC", label: "Qwen 2.5 · 1.5B (default)", note: "~1.0 GB · best answers, needs ~3 GB free RAM", approxGB: 1.0,
    gguf: "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf" },
  { id: "Qwen2.5-0.5B-Instruct-q4f16_1-MLC", label: "Qwen 2.5 · 0.5B (light)", note: "~0.5 GB · for older / low-RAM phones", approxGB: 0.5,
    gguf: "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf" },
  { id: "Llama-3.2-1B-Instruct-q4f16_1-MLC", label: "Llama 3.2 · 1B", note: "~0.8 GB · alternative to Qwen", approxGB: 0.8,
    gguf: "https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf" },
];
// The network's inference carve: 25% of every 300 ANM block reward. Settlement anchors
// (posted automatically, ~10 min cadence) split the WHOLE carve pro-rata across servers
// by earned weight — this is what serving "counts toward".
const CARVE_ANM_PER_BLOCK = 75;
const BUILD = "v3";   // shown in the panel so a user can tell a stale cached tab from the live page

type Phase = "idle" | "loading" | "serving" | "paused" | "stopped" | "error";

interface Stats {
  won: number;
  lost: number;
  tokensOut: number;
  lastTokS: number | null;
  pendingANM: number | null;   // unpaid — next anchor's weight
  paidANM: number | null;      // settled on-chain by ANMSETL1 anchors
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
  if (msg === "stop") { stopped = true; try { engine && engine.e && engine.e.interruptGenerate && engine.e.interruptGenerate(); } catch (e) {} }
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
// CPU prefill is the bottleneck: ~10KB flattened prompts are ~2.6k tokens, which a
// single wasm thread chews through for MINUTES. Clamp much harder for the wasm
// engine (instructions head + the recent tail with the actual question survive).
function promptBudget(cfg) {
  // The GPU prebuilds carry a 4k-token context: leave ~2k tokens of OUTPUT room
  // (13000 chars of prompt used to eat ~3.3k tokens and starve the answer).
  if (cfg.engineKind !== "wllama") return 7500;
  // Keep CPU jobs under ~a minute end-to-end: prefill dominates, so keep the
  // prompt small even with threads (the head instructions + the tail question
  // are what matter; the middle history is the safest cut).
  const threaded = typeof crossOriginIsolated !== "undefined" && crossOriginIsolated;
  return threaded ? 3600 : 2400;
}
let engineModelId = null;
let engineKind = null;
function unloadEngine() {
  try { engine && engine.e && engine.e.unload && engine.e.unload(); } catch (e) {}
  try { engine && engine.w && engine.w.exit && engine.w.exit(); } catch (e) {}
  engine = null; engineModelId = null; engineKind = null;
}
async function ensureEngine(cfg) {
  if (engine && engineModelId === cfg.modelId && engineKind === cfg.engineKind) return;
  unloadEngine();
  if (cfg.engineKind === "wllama") {
    // CPU / WebAssembly lane (no WebGPU needed — iPhone Safari runs this).
    // wllama's wasm loader has one unguarded document.baseURI read; give a
    // WORKER the minimum it dereferences (an https base, never blob:). Scoped
    // HERE because a fake global document breaks WebLLM's environment
    // detection (it then resolves URLs against the blob: worker base —
    // "Failed to construct URL: Invalid URL" on every GPU laptop).
    if (typeof document === "undefined") {
      globalThis.document = { baseURI: cfg.pageHref || "https://pool.animica.org/serve", currentScript: null };
    }
    post({ type: "status", text: "Loading the CPU engine (WebAssembly)…" });
    const mod = await import(cfg.wllamaUrl);
    if (stopped) return;
    const w = new mod.Wllama(cfg.wllamaWasm);
    post({ type: "status", text: "Downloading the model (cached after the first time)…" });
    await w.loadModelFromUrl(cfg.ggufUrl, {
      n_ctx: 6144,
      useCache: true,
      progressCallback: (pr) => {
        if (stopped || !pr || !pr.total) return;
        post({ type: "progress", pct: Math.round(100 * pr.loaded / pr.total),
               text: "Fetching " + (pr.loaded / 1e6).toFixed(0) + " / " + (pr.total / 1e6).toFixed(0) + " MB" });
      },
    });
    engine = { kind: "wllama", w: w };
    const threaded = typeof crossOriginIsolated !== "undefined" && crossOriginIsolated;
    post({ type: "log", text: "CPU engine ready · " + (threaded ? "multi-threaded (" + ((navigator.hardwareConcurrency || 4)) + " cores)" : "single-threaded (no cross-origin isolation)") });
  } else {
    post({ type: "status", text: "Loading WebLLM…" });
    const webllm = await import(cfg.webllmUrl);
    if (stopped) return;
    post({ type: "status", text: "Downloading the model (cached after the first time)…" });
    const e = await webllm.CreateMLCEngine(cfg.modelId, {
      initProgressCallback: (p) => {
        if (stopped) return;
        post({ type: "progress", pct: typeof p.progress === "number" ? Math.round(p.progress * 100) : null,
               text: p.text ? String(p.text).slice(0, 90) : null });
      },
    });
    engine = { kind: "webllm", e: e };
  }
  engineModelId = cfg.modelId;
  engineKind = cfg.engineKind;
}
function interruptEngine() {
  try { engine && engine.e && engine.e.interruptGenerate && engine.e.interruptGenerate(); } catch (e) {}
}
async function generateText(prompt, maxTok, temperature, topP, onToken) {
  if (engine.kind === "wllama") {
    const out = await engine.w.createChatCompletion(
      [{ role: "user", content: prompt }],
      { nPredict: maxTok, sampling: { temp: temperature, top_p: topP },
        onNewToken: (tok, piece, currentText) => { onToken(1); } });
    return String(out || "");
  }
  let text = "";
  const chunks = await engine.e.chat.completions.create({
    messages: [{ role: "user", content: prompt }],
    max_tokens: maxTok,
    temperature: temperature,
    top_p: topP,
    stream: true,
  });
  for await (const c of chunks) {
    if (stopped) break;
    const piece = (c && c.choices && c.choices[0] && c.choices[0].delta && c.choices[0].delta.content) || "";
    if (piece) { text += piece; onToken(1); }
  }
  return text;
}
export async function download(cfg) {
  try {
    await ensureEngine(cfg);
    if (stopped) return;
    post({ type: "downloaded", modelId: cfg.modelId });
    post({ type: "log", text: "model ready: " + cfg.modelId });
  } catch (e) {
    post({ type: "fatal", text: String(e && e.message || e).slice(0, 200) });
  }
}
export async function run(cfg) {
  try {
    await ensureEngine(cfg);
    if (stopped) { unloadEngine(); return; }
    post({ type: "ready" });
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
          .then((e) => {
            const pendingCum = Number(e && e.earnings_pending_animica || 0);
            const paid = Number(e && e.earnings_paid_animica || 0);
            const unpaid = e && e.earnings_unpaid_animica != null
              ? Number(e.earnings_unpaid_animica) : Math.max(0, pendingCum - paid);
            post({ type: "earnings", pending: unpaid, paid: paid, completed: Number(e && e.jobs_completed || 0) });
          })
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
      const prompt = clampPrompt(String(job.prompt || ""), promptBudget(cfg));
      if (!prompt.trim()) { post({ type: "log", text: "claimed " + job.job_id.slice(0, 10) + "… but it carried no prompt — skipped" }); continue; }
      // Output budget: enough for complete code/answers. The CPU engine scales
      // with its thread mode — multithreaded wasm sustains ~450 tokens inside
      // the claim window; single-thread gets less so answers still finish.
      // Near-unlimited within physics: the ceiling is the model context minus the
      // clamped prompt, and the wall-clock is covered by the 600s claim lease +
      // matching bridge/node windows. Multithreaded CPU sustains ~1.5k tokens.
      const engineCap = cfg.engineKind === "wllama"
        ? ((typeof crossOriginIsolated !== "undefined" && crossOriginIsolated) ? 1536 : 768)
        : cfg.maxOutputCap;
      const maxTok = Math.max(16, Math.min(Number(job.max_output_tokens) || 2048, engineCap));
      const deadline = Number(job.claim_expires_at) > 0 ? Number(job.claim_expires_at) * 1000 : Date.now() + 120000;
      post({ type: "status", text: "Answering job " + job.job_id.slice(0, 10) + "… (" + prompt.length + " chars in, ≤" + maxTok + " tokens out)" });
      post({ type: "log", text: "claimed " + job.job_id.slice(0, 10) + "… tier=" + job.tier });
      let text = "";
      let tokens = 0;
      const t0 = Date.now();
      const watchdog = setTimeout(interruptEngine, Math.max(5000, deadline - Date.now() - 4000));
      try {
        text = await generateText(
          prompt, maxTok,
          Math.max(0, Math.min(Number(job.temperature != null ? job.temperature : 0.3), 1.2)),
          Math.max(0.05, Math.min(Number(job.top_p != null ? job.top_p : 0.9), 1)),
          (n) => { tokens += n; });
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
    unloadEngine();
  }
  post({ type: "stopped" });
}
`;

const WORKER_HARNESS = `
${CORE_SOURCE.replace("__POST__(m)", "self.postMessage(m)")}
self.onmessage = (ev) => {
  const d = ev.data || {};
  if (d.type === "start") run(d.cfg);
  else if (d.type === "download") download(d.cfg);
  else if (d.type === "control") __control(d.cmd);
};
self.postMessage({ type: "boot", webgpu: !!(self.navigator && self.navigator.gpu) });
`;

function makeInlineModule(): string {
  // Same core, main-thread flavor: post() calls a global the component registers.
  return CORE_SOURCE.replace("__POST__(m)", "(globalThis.__anmServePost || (() => {}))(m)");
}

// Real bech32m validation (BIP-350). This matters: workers may register ANY string,
// but settlement anchors can only pay valid anim1… addresses — a typo'd address would
// accrue IOUs that can never be paid out, so Start refuses invalid ones outright.
const B32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";
function bech32Polymod(values: number[]): number {
  const GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
  let chk = 1;
  for (const v of values) {
    const b = chk >>> 25;
    chk = ((chk & 0x1ffffff) << 5) ^ v;
    for (let i = 0; i < 5; i++) if ((b >>> i) & 1) chk ^= GEN[i];
  }
  return chk >>> 0;
}
function isValidAnimAddress(addr: string): boolean {
  const a = addr.trim();
  if (a !== a.toLowerCase() && a !== a.toUpperCase()) return false;
  const s = a.toLowerCase();
  const pos = s.lastIndexOf("1");
  if (!s.startsWith("anim1") || pos !== 4 || s.length < pos + 7) return false;
  const hrp = s.slice(0, pos);
  const data: number[] = [];
  for (const ch of s.slice(pos + 1)) {
    const d = B32_CHARSET.indexOf(ch);
    if (d === -1) return false;
    data.push(d);
  }
  const hrpExpand = [...[...hrp].map((c) => c.charCodeAt(0) >>> 5), 0, ...[...hrp].map((c) => c.charCodeAt(0) & 31)];
  return bech32Polymod([...hrpExpand, ...data]) === 0x2bc830a3; // bech32m constant
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
  const [screenAwake, setScreenAwake] = useState<boolean>(true);
  const [batterySupported, setBatterySupported] = useState<boolean>(false);
  const [charging, setCharging] = useState<boolean>(true);
  const [gpuOk, setGpuOk] = useState<boolean | null>(null);
  const [modelReady, setModelReady] = useState<boolean>(false);
  const [downloading, setDownloading] = useState<boolean>(false);
  const [mode, setMode] = useState<"worker" | "inline" | null>(null);
  const [stats, setStats] = useState<Stats>({ won: 0, lost: 0, tokensOut: 0, lastTokS: null, pendingANM: null, paidANM: null, jobsCompleted: null });
  const [log, setLog] = useState<string[]>([]);

  const workerRef = useRef<Worker | null>(null);
  const inlineRef = useRef<any>(null);           // inline module (Safari fallback)
  const runRef = useRef<number>(0);
  const wakeLockRef = useRef<any>(null);
  const audioRef = useRef<{ ctx: AudioContext; osc: OscillatorNode } | null>(null);
  const phaseRef = useRef<Phase>("idle");
  useEffect(() => { phaseRef.current = phase; }, [phase]);
  const [dlSpeed, setDlSpeed] = useState<number | null>(null);   // MB/s while downloading
  const dlRef = useRef<{ pct: number; t: number } | null>(null);
  const modelIdRef = useRef(modelId);
  useEffect(() => { modelIdRef.current = modelId; }, [modelId]);

  const addLog = useCallback((line: string) => {
    const t = new Date().toLocaleTimeString();
    setLog((l) => [`${t}  ${line}`, ...l].slice(0, 60));
  }, []);

  // ── environment probes + persisted prefs ──────────────────────────────────
  const [engineKind, setEngineKind] = useState<"webllm" | "wllama">("webllm");
  // Payout cadence feed (written by the settlement-anchor worker after every run):
  // anchors post ~every block (~95s) whenever there was ANY new inference, moving
  // the whole 75 ANM carve to providers pro-rata.
  const [payoutFeed, setPayoutFeed] = useState<any>(null);
  const [countdown, setCountdown] = useState<number | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => fetch("/serve-payouts.json", { cache: "no-cache" })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (alive && j) setPayoutFeed(j); })
      .catch(() => { /* */ });
    load();
    const poll = setInterval(load, 30000);
    const tick = setInterval(() => {
      setPayoutFeed((f: any) => { if (f) setCountdown(Math.max(0, Math.round(f.next_eta_ts - Date.now() / 1000))); return f; });
    }, 1000);
    return () => { alive = false; clearInterval(poll); clearInterval(tick); };
  }, []);
  useEffect(() => {
    const hasGpu = typeof navigator !== "undefined" && !!(navigator as any).gpu;
    setGpuOk(hasGpu);
    // No WebGPU (iPhone Safari, Firefox, older Android) → the CPU/WebAssembly engine.
    // ?engine=wasm|webgpu overrides for testing.
    const forced = new URLSearchParams(window.location.search).get("engine");
    const kind = forced === "wasm" ? "wllama" : forced === "webgpu" ? "webllm" : hasGpu ? "webllm" : "wllama";
    setEngineKind(kind);
    try {
      const a = localStorage.getItem("anmServeAddress"); if (a) setAddress(a);
      const m = localStorage.getItem("anmServeModel");
      if (m && MODELS.some((x) => x.id === m)) setModelId(m);
      else if (kind === "wllama") setModelId(MODELS[1].id);   // CPU: default to the 0.5B
      const c = localStorage.getItem("anmServeChargeOnly"); if (c != null) setChargeOnly(c !== "0");
      const b = localStorage.getItem("anmServeBgMode"); if (b != null) setBgMode(b !== "0");
      const w = localStorage.getItem("anmServeScreenAwake"); if (w != null) setScreenAwake(w !== "0");
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
  useEffect(() => { try { localStorage.setItem("anmServeModel", modelId); } catch { /* */ } setModelReady(false); }, [modelId]);
  useEffect(() => { try { localStorage.setItem("anmServeChargeOnly", chargeOnly ? "1" : "0"); } catch { /* */ } }, [chargeOnly]);
  useEffect(() => { try { localStorage.setItem("anmServeBgMode", bgMode ? "1" : "0"); } catch { /* */ } }, [bgMode]);
  const screenAwakeRef = useRef(screenAwake);
  useEffect(() => {
    screenAwakeRef.current = screenAwake;
    try { localStorage.setItem("anmServeScreenAwake", screenAwake ? "1" : "0"); } catch { /* */ }
    // Live toggle: releasing lets the screen sleep — serving continues on the
    // background worker (keep the audio keepalive on for reliability); the
    // checkbox click is a user gesture, so re-acquiring works too.
    if (phaseRef.current === "serving" || phaseRef.current === "paused" || phaseRef.current === "loading") {
      if (screenAwake) void acquireWakeLock(); else releaseWakeLock();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screenAwake]);

  const cfgRef = useRef<any>(null);
  const signOff = useCallback((addr: string) => {
    // Best-effort "I'm gone" to the queue: removes this worker from every online
    // view immediately (its earnings ledger is kept server-side). keepalive lets
    // the request finish while the page is being torn down.
    try {
      fetch(RPC_URL, {
        method: "POST",
        keepalive: true,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "aicf.workerSignOff", params: { address: addr } }),
      }).catch(() => { /* */ });
    } catch { /* */ }
  }, []);

  // Closing / navigating away must stop serving (the worker thread dies with the
  // page anyway) AND tell the network immediately. Coming back from bfcache
  // re-registers so the fleet view stays truthful in both directions.
  useEffect(() => {
    const onHide = () => {
      if ((phaseRef.current === "serving" || phaseRef.current === "paused" || phaseRef.current === "loading") && cfgRef.current) {
        signOff(cfgRef.current.address);
      }
    };
    const onShow = (e: PageTransitionEvent) => {
      if (e.persisted && (phaseRef.current === "serving" || phaseRef.current === "paused") && cfgRef.current) {
        fetch(RPC_URL, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "aicf.workerRegister", params: { address: cfgRef.current.address, tiers: cfgRef.current.tiers, hardware: cfgRef.current.hardware } }),
        }).catch(() => { /* */ });
      }
    };
    window.addEventListener("pagehide", onHide);
    window.addEventListener("pageshow", onShow as any);
    return () => { window.removeEventListener("pagehide", onHide); window.removeEventListener("pageshow", onShow as any); };
  }, [signOff]);

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
    if (!screenAwakeRef.current) return;   // user allows the screen to sleep
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
      case "progress": {
        setProgress(m.pct ?? null);
        if (m.text) setStatus(m.text + "…");
        // Download speed estimate: Δprogress × approximate model size / Δt. WebLLM's
        // progress is fetch-dominated, so this tracks real network throughput closely.
        if (typeof m.pct === "number") {
          const now = performance.now();
          const prev = dlRef.current;
          if (prev && m.pct > prev.pct && now - prev.t > 400) {
            const gb = MODELS.find((x) => x.id === modelIdRef.current)?.approxGB ?? 1.0;
            const bytes = ((m.pct - prev.pct) / 100) * gb * 1e9;
            setDlSpeed(bytes / ((now - prev.t) / 1000) / 1e6);
            dlRef.current = { pct: m.pct, t: now };
          } else if (!prev || m.pct < prev.pct) {
            dlRef.current = { pct: m.pct, t: now };
          }
        }
        break;
      }
      case "ready": setProgress(null); setDlSpeed(null); dlRef.current = null; setDownloading(false); setModelReady(true); setPhase("serving"); break;
      case "downloaded":
        setProgress(null); setDlSpeed(null); dlRef.current = null; setDownloading(false); setModelReady(true);
        setPhase("idle");
        setStatus("model ready ✓ — enter your payout address and press Start serving");
        break;
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
      case "earnings": setStats((s) => ({ ...s, pendingANM: m.pending, paidANM: m.paid ?? s.paidANM, jobsCompleted: m.completed })); break;
      case "fatal": setPhase("error"); setStatus(`Stopped: ${m.text}`); setDownloading(false); cleanup(); break;
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

  // Spawn (or reuse) the engine worker; returns "worker" | "inline" | null.
  const ensureWorker = useCallback(async (): Promise<"worker" | "inline" | null> => {
    if (workerRef.current) return "worker";
    if (inlineRef.current) return "inline";
    try {
      const blob = new Blob([WORKER_HARNESS], { type: "text/javascript" });
      const w = new Worker(URL.createObjectURL(blob), { type: "module" });
      const booted: boolean = await new Promise((resolve) => {
        const t = setTimeout(() => resolve(false), 4000);
        w.onmessage = (ev) => { if (ev.data?.type === "boot") { clearTimeout(t); resolve(!!ev.data.webgpu); } };
        w.onerror = () => { clearTimeout(t); resolve(false); };
      });
      if (booted || engineKind === "wllama") {   // the wasm engine needs no WebGPU in the worker
        workerRef.current = w;
        setMode("worker");
        w.onmessage = (ev) => onMessage(ev.data);
        w.onerror = (e) => { setPhase("error"); setStatus(`Worker error: ${e.message || e}`); setDownloading(false); cleanup(); };
        return "worker";
      }
      w.terminate();
    } catch { /* fall through */ }
    try {
      setMode("inline");
      (globalThis as any).__anmServePost = onMessage;
      const blob = new Blob([makeInlineModule()], { type: "text/javascript" });
      const mod: any = await import(/* webpackIgnore: true */ URL.createObjectURL(blob));
      inlineRef.current = mod;
      return "inline";
    } catch {
      return null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onMessage, engineKind]);

  const baseCfg = useCallback(() => ({
    address: address.trim(),
    modelId,
    tiers: TIERS,
    rpcUrl: RPC_URL,
    webllmUrl: WEBLLM_URL,
    engineKind,
    wllamaUrl: WLLAMA_URL,
    wllamaWasm: WLLAMA_WASM,
    ggufUrl: MODELS.find((x) => x.id === modelId)?.gguf || MODELS[1].gguf,
    // CPU generation is slow: cap output harder so answers land inside the claim window.
    maxOutputCap: 2048,   // GPU engine ceiling; per-engine CPU caps decided in the core
    pageHref: window.location.href,
    hardware: {
      engine: engineKind,
      model: modelId,
      ua: navigator.userAgent.slice(0, 160),
      platform: (navigator as any).userAgentData?.platform || navigator.platform || "",
      cores: navigator.hardwareConcurrency || 0,
      device_memory_gb: (navigator as any).deviceMemory || 0,
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [address, modelId, engineKind]);

  // "Download model" — no address needed; fetches + compiles, then waits for Start.
  const downloadModel = useCallback(async () => {
    setDownloading(true);
    setPhase("loading");
    setStatus("Preparing download…");
    setProgress(0);
    const cfg = baseCfg();
    const kind = await ensureWorker();
    if (kind === "worker") workerRef.current!.postMessage({ type: "download", cfg });
    else if (kind === "inline") void inlineRef.current.download(cfg);
    else { setPhase("error"); setStatus("Couldn't start the engine in this browser."); setDownloading(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseCfg, ensureWorker]);

  // ── start / stop ──────────────────────────────────────────────────────────
  const start = useCallback(async () => {
    if (!address.trim()) { setStatus("Enter the anim1… address that should be paid."); return; }
    if (!isValidAnimAddress(address)) {
      setPhase("error");
      setStatus("That address fails the bech32m checksum, so the settlement anchors can NEVER pay it. Paste the exact anim1… address from your wallet (animica.org/wallet).");
      return;
    }
    runRef.current += 1;
    const cfg = baseCfg();
    cfgRef.current = cfg;
    setPhase("loading");
    setStatus(modelReady ? "Starting…" : "Starting (downloading the model first)…");
    if (!modelReady) setProgress(0);
    await acquireWakeLock();
    if (bgMode) startKeepalive();
    const kind = await ensureWorker();
    if (kind === "worker") {
      workerRef.current!.postMessage({ type: "start", cfg });
      addLog("running in a background worker — serving continues while the tab is hidden");
    } else if (kind === "inline") {
      addLog("this browser has no WebGPU in workers — running in the page (keep this tab in the foreground)");
      void inlineRef.current.run(cfg);
    } else {
      setPhase("error");
      setStatus("Couldn't start the engine in this browser.");
      cleanup();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [address, modelReady, bgMode, baseCfg, ensureWorker, addLog]);

  const stop = useCallback(() => {
    sendControl("stop");
    if (cfgRef.current) signOff(cfgRef.current.address);
    // give the core a moment to unload the engine, then hard-terminate
    window.setTimeout(() => cleanup(), 2500);
    setPhase("stopped");
    setStatus("Stopped. Your pending earnings stay on your address.");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sendControl]);

  useEffect(() => () => { sendControl("stop"); if (cfgRef.current) signOff(cfgRef.current.address); cleanup(); }, []); // unmount
  // eslint-disable-next-line react-hooks/exhaustive-deps

  const running = phase === "loading" || phase === "serving" || phase === "paused";

  return (
    <div className="space-y-4 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
      {engineKind === "wllama" && (
        <div className="rounded-lg border border-sky-400/40 bg-sky-400/10 p-3 text-sm text-sky-200">
          No WebGPU in this browser (iPhone Safari included) — running the <strong>CPU engine
          (WebAssembly)</strong> instead. It works everywhere, just slower: pick the 0.5B model,
          expect a few tokens per second, and you&apos;ll mostly win jobs when faster workers are
          asleep. Chrome/Edge on Android or desktop unlock the faster GPU engine.
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
          {address.trim() ? (
            isValidAnimAddress(address) ? (
              <span className="mt-1 block text-xs text-neon-green">
                ✓ valid address — earnings will credit here ·{" "}
                <a className="underline hover:text-white" target="_blank" rel="noreferrer"
                   href={`https://explorer.animica.org/address/${address.trim()}`}>view on explorer</a>
              </span>
            ) : (
              <span className="mt-1 block text-xs text-red-400">✗ not a valid anim1… address (bech32m checksum fails) — it could never be paid</span>
            )
          ) : (
            <span className="mt-1 block text-xs text-white/40">enter your payout address to enable Start</span>
          )}
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
        {!running && (
          <button
            onClick={downloadModel}
            disabled={downloading || modelReady}
            className="rounded-xl border border-neon-green/60 px-5 py-2.5 font-semibold text-neon-green transition hover:bg-neon-green/10 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {modelReady ? "Model ready ✓" : downloading ? "Downloading…" : "Download model"}
          </button>
        )}
        {!running ? (
          <div className="flex flex-col">
            <button
              onClick={start}
              disabled={!address.trim() || !isValidAnimAddress(address)}
              className="rounded-xl bg-neon-green px-6 py-2.5 font-semibold text-black transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Start serving
            </button>
            {(!address.trim() || !isValidAnimAddress(address)) && (
              <span className="mt-1 text-[11px] text-white/40">
                {!address.trim() ? "waiting for your payout address" : "fix the address above first"}
              </span>
            )}
          </div>
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
        <label className="flex items-center gap-2 text-sm text-white/70" title="On: holds a wake-lock so the screen stays on (most reliable). Off: the screen may sleep; on Android the background worker + keepalive tone keep serving while plugged in. iPhone suspends the tab when the screen sleeps — leave this on there.">
          <input type="checkbox" checked={screenAwake} onChange={(e) => setScreenAwake(e.target.checked)} className="h-4 w-4 accent-[#14C79B]" />
          Keep screen awake
        </label>
      </div>

      <div className="rounded-lg border border-white/10 bg-black/30 p-3 font-mono text-sm text-white/80">
        <span className={
          phase === "serving" ? "text-neon-green" : phase === "paused" ? "text-amber-300" : phase === "error" ? "text-red-400" : "text-white/60"
        }>
          {phase === "idle" ? (status || "idle — enter an address and press Start") : status || phase}
        </span>
        {phase === "serving" && mode === "worker" && (
          <span className="ml-2 text-xs text-white/40">· background-capable worker</span>
        )}
        <span className="float-right text-[10px] text-white/25">{BUILD}</span>
        {progress != null && (
          <div className="mt-2 space-y-1">
            <div className="h-1.5 w-full overflow-hidden rounded bg-white/10">
              <div className="h-full bg-neon-green transition-all" style={{ width: `${progress}%` }} />
            </div>
            <div className="flex justify-between text-xs text-white/50">
              <span>downloading model · {progress}%</span>
              <span>{dlSpeed != null ? `${dlSpeed.toFixed(1)} MB/s` : "…"}</span>
            </div>
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 text-center md:grid-cols-3 lg:grid-cols-6">
        <StatBox label="jobs won" value={String(stats.won)} accent />
        <StatBox label="races lost" value={String(stats.lost)} />
        <StatBox label="tokens out" value={stats.tokensOut.toLocaleString()} />
        <StatBox label="speed" value={stats.lastTokS ? `${stats.lastTokS.toFixed(1)} tok/s` : "—"} />
        <StatBox label="pending ANM" value={fmtANM(stats.pendingANM)} note="queued for the next payout" />
        {isValidAnimAddress(address) ? (
          <a href={`https://explorer.animica.org/address/${address.trim()}`} target="_blank" rel="noreferrer" className="block">
            <StatBox label="paid out ANM ↗" value={fmtANM(stats.paidANM)} accent note="on-chain · tap to verify" />
          </a>
        ) : (
          <StatBox label="paid out ANM" value={fmtANM(stats.paidANM)} accent />
        )}
      </div>
      {payoutFeed && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-xs text-white/60">
          <span>
            next payout window in{" "}
            <span className="font-mono text-neon-green">
              {countdown != null ? `${Math.floor(countdown / 60)}:${String(countdown % 60).padStart(2, "0")}` : "…"}
            </span>
          </span>
          <span>· every block (~95s) with any inference moves the whole {payoutFeed.carve_anm} ANM carve to providers, pro-rata</span>
          {payoutFeed.last_anchor_ts > 0 && (
            <span>
              · last payout {Math.max(0, Math.round((Date.now() / 1000 - payoutFeed.last_anchor_ts) / 60))} min ago
              {payoutFeed.last_anchor_txid ? (
                <>{" "}(<a className="underline hover:text-white" target="_blank" rel="noreferrer"
                  href={`https://explorer.animica.org/tx/${payoutFeed.last_anchor_txid}`}>tx ↗</a>)</>
              ) : null}
            </span>
          )}
        </div>
      )}
      <p className="text-xs text-white/40">
        Serving counts toward the network&apos;s <strong className="text-white/60">inference carve — {CARVE_ANM_PER_BLOCK} ANM
        per block</strong> (25% of the block reward): every block with ANY new inference gets a settlement anchor that
        moves the whole carve to providers, split pro-rata by earned weight; blocks without inference roll it to the treasury.
        {stats.jobsCompleted != null && (
          <> Ledger for this address: {stats.jobsCompleted} jobs completed all-time, {fmtANM(stats.pendingANM)} ANM pending.</>
        )}
      </p>

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

function StatBox({ label, value, accent = false, note }: { label: string; value: string; accent?: boolean; note?: string }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.03] px-2 py-3 transition hover:border-white/20">
      <div className={`text-lg font-semibold ${accent ? "text-neon-green" : "text-white"}`}>{value}</div>
      <div className="mt-0.5 text-[11px] uppercase tracking-wider text-white/40">{label}</div>
      {note && <div className="mt-0.5 text-[10px] text-white/30">{note}</div>}
    </div>
  );
}
