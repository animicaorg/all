import { NextRequest, NextResponse } from "next/server";
import { chatCompletion } from "@/src/services/llmClient";
import { callDaRpc, DaRpcError } from "@/src/server/da/rpc-client";
import { callWorkRpc } from "@/src/server/work/rpc-client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface InMsg { role: "user" | "assistant" | "tool"; content: string }
interface ToolCallReq {
  tool: string;
  args?: Record<string, unknown>;
}

interface Body {
  messages?: InMsg[];
  tier?: string;
  toolCall?: ToolCallReq;
}

// ─── Per-IP rate limit ───────────────────────────────────────────────
// Cheap in-memory bucket. The landing chat is anonymous so we can't
// per-account it; per-IP keeps the worst abuse to ~12/min.
const RATE_BUCKET = new Map<string, { count: number; resetAt: number }>();
const RATE_WINDOW_MS = 60_000;
const RATE_MAX = 12;

function rateLimit(ip: string): { ok: boolean; remaining: number; resetIn: number } {
  const now = Date.now();
  const cur = RATE_BUCKET.get(ip);
  if (!cur || now > cur.resetAt) {
    RATE_BUCKET.set(ip, { count: 1, resetAt: now + RATE_WINDOW_MS });
    return { ok: true, remaining: RATE_MAX - 1, resetIn: RATE_WINDOW_MS };
  }
  if (cur.count >= RATE_MAX) {
    return { ok: false, remaining: 0, resetIn: cur.resetAt - now };
  }
  cur.count++;
  return { ok: true, remaining: RATE_MAX - cur.count, resetIn: cur.resetAt - now };
}

// ─── Tool implementations ─────────────────────────────────────────────
//
// Every tool here is deterministic, read-only (with one exception for
// da.putBlob), and bounded — no chain mutations or expensive operations.
// Each returns a string that lands directly in the chat as an assistant
// "tool result" message.

function fmtBytes(n: number): string {
  if (!n) return "0 B";
  const u = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 ? 2 : v < 100 ? 1 : 0)} ${u[i]}`;
}

function fmtAnm(nano: bigint | string): string {
  const n = typeof nano === "bigint" ? nano : BigInt(String(nano || "0"));
  const whole = n / 1_000_000_000n;
  const frac = (n % 1_000_000_000n).toString().padStart(9, "0").replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : whole.toString();
}

const TOOLS: Record<string, () => Promise<string>> = {
  chain_head: async () => {
    const h: any = await callWorkRpc("chain.getHead");
    const height = Number(h.height ?? h.block_height ?? 0);
    const hash = String(h.hash ?? "?");
    const ts = h.timestamp ?? null;
    const age = ts ? `${Math.max(0, Math.floor(Date.now() / 1000 - Number(ts)))}s ago` : "unknown age";
    return `**Chain head**\n\n- Height: \`#${height.toLocaleString()}\`\n- Hash: \`${hash}\`\n- ${age}`;
  },

  recent_blocks: async () => {
    const h: any = await callWorkRpc("chain.getHead");
    const head = Number(h.height ?? h.block_height ?? 0);
    const targets = Array.from({ length: 5 }, (_, i) => head - i);
    const rows = await Promise.all(targets.map(async (height) => {
      try {
        const b: any = await callWorkRpc("chain.getBlockByHeight", { height });
        const block = b?.block ?? b;
        return `- \`#${height}\` · ${(block?.transactions?.length ?? block?.txCount ?? 0)} tx · ${block?.size ? fmtBytes(Number(block.size)) : "?"} · \`${String(block?.hash ?? "").slice(0, 16)}…\``;
      } catch (e: any) {
        return `- \`#${height}\` · unavailable (${e?.message?.slice(0, 60) ?? "err"})`;
      }
    }));
    return `**Last 5 blocks**\n\n${rows.join("\n")}`;
  },

  recent_jobs: async () => {
    const r: any = await callWorkRpc("aicf.work.listJobs", { limit: 5 });
    const jobs = r?.jobs ?? [];
    if (jobs.length === 0) return "No recent useful-work jobs on the network.";
    return `**${jobs.length} recent useful-work jobs**\n\n` + jobs.slice(0, 5).map((j: any) =>
      `- \`${j.id}\` · ${j.status} · ${j.rewardAmountAnm} ANM · ${j.title?.slice(0, 60) ?? "(no title)"}`
    ).join("\n");
  },

  list_workers: async () => {
    const r: any = await callWorkRpc("aicf.work.listWorkers", { limit: 10 });
    const ws = r?.workers ?? [];
    if (ws.length === 0) return "No workers registered.";
    return `**${ws.length} registered workers**\n\n` + ws.slice(0, 10).map((w: any) => {
      const caps = (w.capabilities ?? []).slice(0, 3).join(", ") || "—";
      return `- \`${String(w.id).slice(0, 16)}…\` · ${w.deviceType ?? "?"} · earned ${w.totalEarnedAnm ?? "0"} ANM · caps: ${caps}`;
    }).join("\n");
  },

  da_status: async () => {
    const s: any = await callDaRpc("da.status");
    return `**Data Availability status**\n\n` +
      `- Enabled: ${s.enabled ? "✓" : "✗"} · writable: ${s.writable ? "✓" : "✗"}\n` +
      `- Used: ${fmtBytes(Number(s.used_bytes ?? 0))} / cap ${fmtBytes(Number(s.max_bytes ?? 0))}\n` +
      `- Blobs stored: ${s.blob_count ?? 0}\n` +
      `- Free on disk: ${fmtBytes(Number(s.free_bytes_fs ?? 0))}\n` +
      `- Eviction policy: ${s.eviction_policy} · on full: ${s.on_full}\n` +
      `- Version: ${s.version}`;
  },

  da_namespaces: async () => {
    const r: any = await callDaRpc("da.listNamespaces");
    const ns = r?.namespaces ?? [];
    if (ns.length === 0) return "No DA namespaces have any commitments yet.";
    return `**${ns.length} DA namespaces**\n\n` + ns.slice(0, 10).map((n: any) =>
      `- \`ns://${n.namespace}\` · ${n.commitments} commitments · ${fmtBytes(Number(n.total_bytes ?? 0))}`
    ).join("\n");
  },

  network_stats: async () => {
    const [head, hr]: any[] = await Promise.all([
      callWorkRpc("chain.getHead").catch(() => null),
      callWorkRpc("chain.getNetworkHashrate").catch(() => null),
    ]);
    const lines: string[] = ["**Network stats**", ""];
    if (head) {
      lines.push(`- Height: \`#${Number(head.height ?? 0).toLocaleString()}\``);
    }
    if (hr) {
      const rate = hr.hashrate ?? hr.networkHashrate ?? 0;
      lines.push(`- Hashrate: ${rate ? `${(Number(rate) / 1e6).toFixed(2)} MH/s` : "unknown"}`);
    }
    return lines.join("\n");
  },
};

// ─── Prompt assembly ──────────────────────────────────────────────────
//
// Keep it tight so the tiny tier has a fighting chance. Tier-aware
// system prompt; the model gets told what it can / cannot do and what
// tools the user has access to (so it can suggest using them).

function buildSystem(tier: string): string {
  // Dense, factual framing. Workers run small (0.5B–7B) chat models that
  // have zero Animica knowledge from pretraining; without concrete facts
  // here, answers about the chain, mining, tokenomics, AICF, etc. are
  // pure hallucination. Keep this terse — every token here is paid for.
  return `You are Animica's helpful guide. The user is talking to you on the studio.animica.org landing page. This reply is being generated by a real Animica network miner (tier=${tier}) and the miner is paid in ANIMICA for serving it.

Animica is a fully decentralized Layer-1 blockchain for verifiable AI and quantum-secure execution:
- Native token: ANIMICA (denominated in 9-decimal nano-units on-chain).
- Consensus: PoIES — Proof of Integrated External Services — hash-share PoW combined with AI / quantum / storage proofs. Public Stratum pool at pool.animica.org:3333.
- Post-quantum signatures: Dilithium3 (ML-DSA-65) for transactions, with SPHINCS+ as a fallback. No ECDSA / secp256k1.
- Execution: deterministic Python-VM smart contracts with gas metering. Required tx fields: kind, data, gasLimit, fee, nonce, chainId.
- AICF (AI Compute Fund): miners earn ANIMICA per inference turn. Tiers: tiny (CPU), small (~7B, single GPU), flagship (16B MoE — recommended), large (datacenter). The CLI ships as the \`animica\` PyPI package.
- DA layer: namespaced data availability via an NMT (Namespaced Merkle Tree). RPC namespace is \`da.*\`.
- RPC namespaces actually exposed by nodes: \`state.*\`, \`chain.*\`, \`miner.*\`, \`tx.*\`, \`aicf.*\`, \`da.*\`. There is no \`animica.*\` namespace — do not invent one.
- To run a miner: install the \`animica\` package, then \`animica mine --pool pool.animica.org:3333 --wallet <addr>\`. Mining can additionally serve AICF jobs when \`ANIMICA_AICF_TIERS\` is set.

The user can also invoke deterministic tools via the chips under the input: chain_head, recent_blocks, recent_jobs, list_workers, da_status, da_namespaces, network_stats. For live on-chain numbers (current height, hashrate, worker list, DA stats) tell the user to click the matching chip rather than guessing.

Style:
- Be concise. 1–3 short paragraphs max unless asked for detail.
- Use markdown when helpful (lists, code).
- Stay grounded in the facts above. If the user asks something Animica-specific that isn't covered here, say so plainly rather than inventing details.`;
}

function flattenHistory(messages: InMsg[]): string {
  return messages.slice(-12).map((m) => {
    const tag = m.role === "user" ? "User" : m.role === "tool" ? "Tool" : "Assistant";
    return `${tag}: ${m.content}`;
  }).join("\n\n");
}

// ─── Route handler ────────────────────────────────────────────────────

export async function POST(req: NextRequest) {
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "local";
  const rl = rateLimit(ip);
  if (!rl.ok) {
    return NextResponse.json(
      {
        error: `Rate limit (${RATE_MAX}/min). Try again in ${Math.ceil(rl.resetIn / 1000)}s.`,
        retryAfter: rl.resetIn,
      },
      { status: 429 },
    );
  }

  let body: Body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }

  // Tool dispatch — no LLM call, just return the formatted result.
  if (body.toolCall?.tool) {
    const name = body.toolCall.tool;
    const tool = TOOLS[name];
    if (!tool) {
      return NextResponse.json({ error: `unknown tool: ${name}` }, { status: 400 });
    }
    const startedAt = Date.now();
    try {
      const text = await tool();
      return NextResponse.json({
        ok: true,
        kind: "tool_result",
        tool: name,
        content: text,
        durationMs: Date.now() - startedAt,
        ratelimit: { remaining: rl.remaining, resetIn: rl.resetIn },
      });
    } catch (e: any) {
      const msg = e instanceof DaRpcError ? `DA: ${e.message}` : (e?.message ?? "tool failed");
      return NextResponse.json({
        ok: false,
        kind: "tool_result",
        tool: name,
        content: `Tool \`${name}\` failed: ${msg}`,
        durationMs: Date.now() - startedAt,
        ratelimit: { remaining: rl.remaining, resetIn: rl.resetIn },
      }, { status: 200 });
    }
  }

  // Chat dispatch — full prompt with history → live miner (or modal/local).
  const messages = Array.isArray(body.messages) ? body.messages : [];
  if (messages.length === 0 || messages[messages.length - 1].role !== "user") {
    return NextResponse.json({ error: "messages[] must end with a user turn" }, { status: 400 });
  }
  const tier = String(body.tier || process.env.ANIMICA_AICF_TIER || "tiny");

  const sys = buildSystem(tier);
  const hist = flattenHistory(messages);
  const composed = `${sys}\n\n=== conversation so far ===\n${hist}\n\nReply as Assistant. One concise turn.`;

  const startedAt = Date.now();
  try {
    const res = await chatCompletion({ prompt: composed, mode: "strict" });
    const manifest = (res as any)?.manifest ?? {};
    return NextResponse.json({
      ok: true,
      kind: "assistant",
      content: res.content || "(no response)",
      source: res.source,
      provider: manifest.provider,
      tier: manifest.tier,
      costAnm: manifest.cost_animica,
      latencyMs: manifest.latency_ms ?? (Date.now() - startedAt),
      durationMs: Date.now() - startedAt,
      ratelimit: { remaining: rl.remaining, resetIn: rl.resetIn },
    });
  } catch (e: any) {
    return NextResponse.json({
      ok: false,
      error: e?.message ?? "chat failed",
    }, { status: 502 });
  }
}
