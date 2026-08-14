#!/usr/bin/env node
// Reference Animica Pool worker agent. Registers the machine, runs a trivial
// benchmark, then loops: heartbeat + claim/complete queued jobs. The MVP only
// serves Animica's controlled models — it does not run arbitrary code.
//
// Usage: WORKER_TOKEN=anm_worker_xxx ANIMICA_API_BASE=https://pool.animica.org \
//        tsx src/agent.ts
import os from "node:os";
import { execSync } from "node:child_process";

const API = (process.env.ANIMICA_API_BASE || "https://pool.animica.org").replace(/\/$/, "");
const TOKEN = process.env.WORKER_TOKEN || "";
const HEARTBEAT_MS = Number(process.env.HEARTBEAT_MS || 30000);
// Set BITTENSOR_EXECUTOR=1 on rigs enrolled as SN51 executors — each
// heartbeat then also reports the executor container's health so the pool
// can pull a dying rig from validator inventory before collateral slashing.
const BT_EXECUTOR = process.env.BITTENSOR_EXECUTOR === "1";

if (!TOKEN) {
  console.error("WORKER_TOKEN is required");
  process.exit(1);
}

const auth = { authorization: `Bearer ${TOKEN}`, "content-type": "application/json" };

async function call(path: string, method = "GET", body?: unknown) {
  const res = await fetch(`${API}${path}`, { method, headers: auth, body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}: ${await res.text()}`);
  return res.json();
}

// Best-effort GPU detection (name + VRAM) so rigs qualify for GPU rentals and
// Bittensor SN51 enrollment without manual data entry. NVIDIA via nvidia-smi;
// on Apple Silicon the chip IS the GPU (Metal, unified memory) — reported for
// the dashboard, while SN51 enrollment stays NVIDIA-only (gated server-side).
function detectGpu(): { gpuModel?: string; vramGb?: number } {
  try {
    const out = execSync("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader", {
      encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    const [name, mem] = (out.split("\n")[0] ?? "").split(",").map((s) => s.trim());
    if (name) {
      const mib = parseInt(mem, 10);
      return { gpuModel: name, vramGb: Number.isFinite(mib) ? Math.round(mib / 1024) : undefined };
    }
  } catch { /* no nvidia-smi — fall through */ }
  if (process.platform === "darwin") {
    try {
      const chip = execSync("sysctl -n machdep.cpu.brand_string", {
        encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "ignore"],
      }).trim();
      if (/^Apple /.test(chip)) {
        return { gpuModel: `${chip} (Metal, unified memory)`, vramGb: Math.round(os.totalmem() / 1024 ** 3) };
      }
    } catch { /* ignore */ }
  }
  return {};
}

// Is the SN51 executor container alive on this rig?
function executorRunning(): boolean {
  try {
    const out = execSync("docker ps --filter ancestor=daturaai/compute-subnet-executor --format '{{.ID}}'", {
      encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    return out.length > 0;
  } catch {
    return false;
  }
}

function benchmarkScore(): number {
  // Trivial CPU micro-benchmark (ops/ms over a fixed loop).
  const start = Date.now();
  let x = 0;
  for (let i = 0; i < 5_000_000; i++) x += Math.sqrt(i);
  const ms = Math.max(1, Date.now() - start);
  return Math.round((5_000_000 / ms) * (os.cpus().length || 1));
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

  setInterval(() => {
    call("/api/workers/heartbeat", "POST").catch((e) => console.error("heartbeat", String(e)));
    if (BT_EXECUTOR) {
      call("/api/bittensor/executor/report", "POST", { running: executorRunning() })
        .catch((e) => console.error("bittensor report", String(e)));
    }
  }, HEARTBEAT_MS);

  // Poll for jobs and (in this reference) immediately complete them.
  setInterval(async () => {
    try {
      const jobs = (await call("/api/workers/jobs")) as Job[];
      for (const j of jobs.slice(0, 1)) {
        try {
          await call(`/api/workers/jobs/${j.id}/claim`, "POST");
        } catch {
          continue; // another worker grabbed it
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

interface Job {
  id: string;
  model?: string;
  payload?: { messages?: { role: string; content: string }[]; prompt?: string; temperature?: number; maxTokens?: number };
}

// Serve the inference job against a local OpenAI-compatible model server
// (e.g. vLLM / Ollama / LM Studio). Configure LOCAL_INFERENCE_URL +
// LOCAL_INFERENCE_MODEL. Without it, the job cannot be served.
async function serve(job: Job): Promise<{ resultText: string; inputTokens: number; outputTokens: number }> {
  const url = process.env.LOCAL_INFERENCE_URL;
  if (!url) throw new Error("LOCAL_INFERENCE_URL not set — worker cannot serve inference");
  // Map the platform model name (e.g. anm-fast-8b) → a real local model.
  // LOCAL_INFERENCE_MODEL_MAP is "k=v,k=v"; LOCAL_INFERENCE_MODEL is fallback.
  const map: Record<string, string> = {};
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
  const j: any = await res.json();
  return {
    resultText: j?.choices?.[0]?.message?.content ?? "",
    inputTokens: j?.usage?.prompt_tokens ?? 0,
    outputTokens: j?.usage?.completion_tokens ?? 0,
  };
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
