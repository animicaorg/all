#!/usr/bin/env node
// Animica Pool - standalone worker agent (no repo / no Docker required).
//   curl -fsSL https://pool.animica.org/worker-agent.mjs -o animica-worker-agent.mjs
//   WORKER_TOKEN=anm_worker_xxx ANIMICA_API_BASE=https://pool.animica.org \
//     LOCAL_INFERENCE_URL=http://localhost:11434/v1 node animica-worker-agent.mjs
//
// Registers the machine, benchmarks, heartbeats, and serves inference jobs
// against a local OpenAI-compatible server (Ollama / LM Studio / vLLM).
// Requires Node 18+ (global fetch). Serves ONLY Animica's controlled models.
import os from "node:os";
import { execSync } from "node:child_process";

const API = (process.env.ANIMICA_API_BASE || "https://pool.animica.org").replace(/\/$/, "");
const TOKEN = process.env.WORKER_TOKEN || "";
const HEARTBEAT_MS = Number(process.env.HEARTBEAT_MS || 15000);

if (!TOKEN) {
  console.error("WORKER_TOKEN is required");
  process.exit(1);
}

const auth = { authorization: `Bearer ${TOKEN}`, "content-type": "application/json" };

async function call(path, method = "GET", body) {
  const res = await fetch(`${API}${path}`, { method, headers: auth, body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status}: ${await res.text()}`);
  return res.json();
}

// Best-effort GPU detection. NVIDIA via nvidia-smi; on Apple Silicon the chip
// IS the GPU (Metal, unified memory) - reported so the dashboard shows real
// hardware. Bittensor SN51 enrollment stays NVIDIA-only (gated server-side).
function detectGpu() {
  try {
    const out = execSync("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader", {
      encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    const [name, mem] = (out.split("\n")[0] ?? "").split(",").map((s) => s.trim());
    if (name) {
      const mib = parseInt(mem, 10);
      return { gpuModel: name, vramGb: Number.isFinite(mib) ? Math.round(mib / 1024) : undefined };
    }
  } catch {}
  if (process.platform === "darwin") {
    try {
      const chip = execSync("sysctl -n machdep.cpu.brand_string", {
        encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "ignore"],
      }).trim();
      if (/^Apple /.test(chip)) {
        return { gpuModel: `${chip} (Metal, unified memory)`, vramGb: Math.round(os.totalmem() / 1024 ** 3) };
      }
    } catch {}
  }
  return {};
}

function benchmarkScore() {
  const start = Date.now();
  let x = 0;
  for (let i = 0; i < 5_000_000; i++) x += Math.sqrt(i);
  const ms = Math.max(1, Date.now() - start);
  return Math.round((5_000_000 / ms) * (os.cpus().length || 1));
}

async function serve(job) {
  const url = process.env.LOCAL_INFERENCE_URL;
  if (!url) throw new Error("LOCAL_INFERENCE_URL not set - worker cannot serve inference");
  const map = {};
  for (const pair of (process.env.LOCAL_INFERENCE_MODEL_MAP || "").split(",")) {
    const [k, v] = pair.split("=").map((s) => s.trim());
    if (k && v) map[k] = v;
  }
  const model = (job.model && map[job.model]) || process.env.LOCAL_INFERENCE_MODEL || job.model || "default";
  const messages = job.payload?.messages ?? [{ role: "user", content: job.payload?.prompt ?? "" }];
  const res = await fetch(`${url.replace(/\/$/, "")}/chat/completions`, {
    method: "POST",
    headers: { "content-type": "application/json", ...(process.env.LOCAL_INFERENCE_KEY ? { authorization: `Bearer ${process.env.LOCAL_INFERENCE_KEY}` } : {}) },
    body: JSON.stringify({ model, messages, temperature: job.payload?.temperature ?? 0.7, max_tokens: job.payload?.maxTokens ?? 512, stream: false }),
  });
  if (!res.ok) throw new Error(`local model HTTP ${res.status}: ${(await res.text()).slice(0, 160)}`);
  const j = await res.json();
  return {
    resultText: j?.choices?.[0]?.message?.content ?? "",
    inputTokens: j?.usage?.prompt_tokens ?? 0,
    outputTokens: j?.usage?.completion_tokens ?? 0,
  };
}

async function main() {
  console.log(`[worker-agent] registering with ${API}`);
  await call("/api/workers/register", "POST", {
    cpuModel: os.cpus()[0]?.model ?? "unknown",
    ramGb: Math.round(os.totalmem() / 1024 ** 3),
    os: `${os.type()} ${os.release()}`,
    ...detectGpu(),
  });
  const score = benchmarkScore();
  await call("/api/workers/benchmark", "POST", { score, details: { cores: os.cpus().length } });
  console.log(`[worker-agent] benchmark=${score}, online. Heartbeating every ${HEARTBEAT_MS}ms.`);
  if (!process.env.LOCAL_INFERENCE_URL) {
    console.log("[worker-agent] NOTE: LOCAL_INFERENCE_URL not set - the worker is online but will fail inference jobs.");
    console.log("[worker-agent]       Install Ollama (https://ollama.com) and run e.g. `ollama serve`, then set");
    console.log("[worker-agent]       LOCAL_INFERENCE_URL=http://localhost:11434/v1 LOCAL_INFERENCE_MODEL=qwen2.5:7b");
  }

  setInterval(() => {
    call("/api/workers/heartbeat", "POST").catch((e) => console.error("heartbeat", String(e)));
  }, HEARTBEAT_MS);

  setInterval(async () => {
    try {
      const jobs = await call("/api/workers/jobs");
      for (const j of jobs.slice(0, 1)) {
        try {
          await call(`/api/workers/jobs/${j.id}/claim`, "POST");
        } catch {
          continue;
        }
        try {
          const out = await serve(j);
          await call(`/api/workers/jobs/${j.id}/complete`, "POST", out);
          console.log(`[worker-agent] completed job ${j.id} (${out.outputTokens} out tok)`);
        } catch (e) {
          await call(`/api/workers/jobs/${j.id}/complete`, "POST", { error: String(e) }).catch(() => {});
          console.error(`[worker-agent] job ${j.id} failed:`, String(e));
        }
      }
    } catch (e) {
      console.error("job poll", String(e));
    }
  }, Math.min(HEARTBEAT_MS, 5000));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
