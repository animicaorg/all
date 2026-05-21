import { spawn } from "node:child_process";
import { getLlmRuntimeConfig } from "@/src/server/llm/runtimeConfig";

type LlmRequest = {
  prompt: string;
  mode: string;
  context?: Record<string, unknown>;
};

export type LlmResponse = {
  content: string;
  abi: unknown[];
  manifest: Record<string, unknown>;
  requestId: string;
  source: "modal" | "aicf" | "local-fallback";
  tokensUsed?: number;
};

export class LlmClientError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly details?: unknown
  ) {
    super(message);
  }
}

const RETRY_STATUSES = new Set([408, 429, 500, 502, 503, 504]);

async function requestWithTimeout(url: string, init: RequestInit, timeoutMs: number) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function withRetry<T>(fn: () => Promise<T>, retries = 2): Promise<T> {
  let lastError: unknown;
  for (let i = 0; i <= retries; i += 1) {
    try {
      return await fn();
    } catch (error: any) {
      lastError = error;
      const status = Number(error?.status || 0);
      if (i >= retries || !RETRY_STATUSES.has(status)) break;
      await new Promise((resolve) => setTimeout(resolve, 300 * 2 ** i));
    }
  }
  throw lastError;
}

function localFallback(input: LlmRequest, reason: string, kind: "contract" | "chat" = "contract"): LlmResponse {
  // The contract-generation path needs a parseable contract bundle so the
  // validator + compile queue don't crash; the chat path needs a normal
  // assistant message. Returning the contract stub for chat traffic
  // surfaces as a `contract Generated { … }` blob in the user's chat
  // window, which is what triggered the "studio chat has no context"
  // bug report.
  if (kind === "chat") {
    return {
      content: [
        "I can't reach the Animica AICF workers right now (and no fallback model is configured), so I can't answer that turn.",
        "",
        `Reason: \`${reason}\`. The site operator can fix this by setting \`ANIMICA_LLM_ENDPOINT_URL\` (Modal) or making \`/usr/local/bin/aicf-chat-once\` available.`,
        "",
        "Meanwhile you can still click the tool chips below the input — they query the chain directly and don't need the inference workers.",
      ].join("\n"),
      abi: [],
      manifest: { provider: "local-fallback", reason, kind: "chat" },
      requestId: crypto.randomUUID(),
      source: "local-fallback",
    };
  }
  return {
    content: `contract Generated {\n  // local fallback (${reason})\n  fn prompt() -> string { return \"${input.prompt.slice(0, 72).replace(/"/g, "'")}\" }\n}`,
    abi: [{ name: "prompt", type: "function" }],
    manifest: { provider: "local-fallback", reason, kind: "contract" },
    requestId: crypto.randomUUID(),
    source: "local-fallback"
  };
}

async function callModalPath(path: string, input: LlmRequest, kind: "contract" | "chat" = "contract"): Promise<LlmResponse> {
  const cfg = getLlmRuntimeConfig();
  if (!cfg.endpointUrl) {
    return localFallback(input, cfg.fallbackReason ?? "missing_endpoint", kind);
  }

  const target = new URL(path, cfg.endpointUrl).toString();
  try {
    return await withRetry(async () => {
      const res = await requestWithTimeout(target, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(input)
      }, 20_000);

      if (!res.ok) {
        const details = await res.text();
        throw new LlmClientError(`Modal request failed (${res.status})`, res.status, details.slice(0, 800));
      }

      const payload = await res.json();
      return {
        content: String(payload.content ?? ""),
        abi: Array.isArray(payload.abi) ? payload.abi : [],
        manifest: typeof payload.manifest === "object" && payload.manifest ? payload.manifest : {},
        requestId: String(payload.requestId ?? crypto.randomUUID()),
        source: "modal"
      } satisfies LlmResponse;
    });
  } catch (error: any) {
    console.error("[llm-client] Modal endpoint unreachable; falling back locally", {
      message: error?.message,
      status: error?.status,
      endpoint: cfg.endpointUrl
    });
    return localFallback(input, "modal_unreachable", kind);
  }
}

// ─── AICF live-miner path ───────────────────────────────────────────────
//
// When Modal isn't configured, prefer the running distributed-aicf workers
// (aka the live miners) over the deterministic local stub. The bridge is a
// tiny python subprocess (/usr/local/bin/aicf-chat-once) that wraps
// agent_runtime.providers.DistributedAICFProvider.serve() — the same code
// path the animica chat CLI uses, so the runner inherits its sign /
// submit / stream / settle handling unchanged.
//
// We deliberately spawn rather than embed Python or call the chain RPC
// directly: it isolates the secret-key plumbing in the existing,
// well-tested Python helpers and keeps the Studio side dependency-free.

const AICF_BIN = process.env.AICF_CHAT_BIN ?? "/usr/local/bin/aicf-chat-once";
const AICF_PYTHON = process.env.AICF_PYTHON ?? "/root/animica/.venv/bin/python";
const AICF_TIMEOUT_MS = Number(process.env.AICF_TIMEOUT_MS ?? 180_000);
const AICF_DISABLED = ["1", "true", "yes"].includes(
  String(process.env.ANIMICA_AICF_DISABLE ?? "").toLowerCase()
);

interface AicfSpec {
  prompt: string;
  tier?: string;
  max_output_tokens?: number;
  temperature?: number;
  wallet_path?: string;
  wallet_label?: string;
  yolo?: boolean;
  rpc_url?: string;
}

interface AicfOk {
  ok: true;
  content: string;
  provider: string;
  // The bech32 id of the miner that actually served this turn — the
  // routing layer ("distributed-aicf") doesn't tell the user *which*
  // miner ran inference, so we surface the real provider_id from the
  // AICF receipt for the UI's "served by X" line.
  miner_id?: string;
  // AICF job id returned by aicf.submitInferenceJob, useful for
  // looking the turn up later via aicf.getJobStatus.
  job_id?: string;
  tier: string;
  requested_tier?: string;
  cost_animica: number;
  latency_ms: number;
  fallback_reasons: string[];
  source: "aicf";
}

interface AicfErr {
  ok: false;
  error: string;
  code: string;
}

async function callAicfOnce(spec: AicfSpec): Promise<AicfOk | AicfErr> {
  return new Promise((resolve) => {
    const child = spawn(AICF_PYTHON, [AICF_BIN], {
      env: {
        ...process.env,
        PYTHONPATH: [
          "/root/animica/ai/agent_runtime/src",
          process.env.PYTHONPATH ?? "",
        ].filter(Boolean).join(":"),
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      resolve({ ok: false, error: `AICF subprocess timeout after ${AICF_TIMEOUT_MS}ms`, code: "TIMEOUT" });
    }, AICF_TIMEOUT_MS);
    child.stdout.on("data", (b) => { stdout += b.toString("utf-8"); });
    child.stderr.on("data", (b) => { stderr += b.toString("utf-8"); });
    child.on("error", (err) => {
      clearTimeout(timer);
      resolve({ ok: false, error: `spawn failed: ${err.message}`, code: "SPAWN" });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      try {
        // Parse the last non-empty line of stdout — the helper guarantees
        // one JSON line per invocation, but a warning from a transitive
        // import (e.g. transformers deprecation) might land first.
        const lines = stdout.trim().split("\n").filter(Boolean);
        const lastLine = lines[lines.length - 1] ?? "";
        const parsed = JSON.parse(lastLine);
        if (parsed && typeof parsed === "object" && "ok" in parsed) {
          resolve(parsed as AicfOk | AicfErr);
          return;
        }
      } catch {
        // fall through to error
      }
      resolve({
        ok: false,
        error: `AICF subprocess exit ${code}, stderr: ${stderr.slice(-400)}`,
        code: "BAD_OUTPUT",
      });
    });
    child.stdin.end(JSON.stringify(spec));
  });
}

async function callAicf(input: LlmRequest): Promise<LlmResponse | null> {
  if (AICF_DISABLED) return null;
  const out = await callAicfOnce({
    prompt: input.prompt,
    tier: process.env.ANIMICA_AICF_TIER,
    max_output_tokens: Number(process.env.ANIMICA_AICF_MAX_TOKENS ?? 1024),
    temperature: Number(process.env.ANIMICA_AICF_TEMPERATURE ?? 0.2),
    wallet_path: process.env.ANIMICA_AICF_WALLET,
    wallet_label: process.env.ANIMICA_AICF_WALLET_LABEL,
    yolo: true,
  });
  if (!out.ok) {
    console.warn("[llm-client] AICF unavailable, will fall through:", out.code, out.error);
    return null;
  }
  return {
    content: out.content,
    abi: [],
    manifest: {
      provider: out.provider,
      miner_id: out.miner_id ?? "",
      job_id: out.job_id ?? "",
      tier: out.tier,
      requested_tier: out.requested_tier ?? "",
      cost_animica: out.cost_animica,
      latency_ms: out.latency_ms,
      fallback_reasons: out.fallback_reasons,
    },
    requestId: crypto.randomUUID(),
    source: "aicf",
  };
}

export async function chatCompletion(input: LlmRequest): Promise<LlmResponse> {
  // Try Modal first when configured; if no Modal URL, try live AICF miners;
  // fall through to the deterministic local stub last.
  const cfg = getLlmRuntimeConfig();
  if (cfg.endpointUrl) {
    return callModalPath("/v1/chat", input, "chat");
  }
  const aicfRes = await callAicf(input);
  if (aicfRes) return aicfRes;
  return localFallback(input, cfg.fallbackReason ?? "missing_endpoint", "chat");
}

export async function generateContractCompletion(input: LlmRequest): Promise<LlmResponse> {
  // Contract generation has a strict ABI/manifest contract that only Modal
  // implements today; don't route it through AICF (yet).
  return callModalPath("/v1/contracts/generate", input, "contract");
}
