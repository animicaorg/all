/**
 * Provider abstraction for the coding agent.
 *
 * Goal: keep the agent core usable offline. We ship an `OfflineProvider` that
 * is deterministic and useful for scaffolding / explanation / patch-shaping,
 * plus a `RemoteProvider` adapter for OpenAI-compatible endpoints (Anthropic
 * Messages, OpenAI Chat Completions, AICF chat endpoint). Real keys come
 * from env at call time so we never persist them.
 *
 * The provider returns text; higher layers translate text into Patch ops.
 */

import type { AgentConfig } from "./config.js";
import { AgentError } from "./errors.js";

export interface AgentMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface CompletionOptions {
  messages: AgentMessage[];
  model?: string;
  maxTokens?: number;
  temperature?: number;
  /** AbortSignal to cancel pending HTTP. */
  signal?: AbortSignal;
}

export interface CompletionResult {
  text: string;
  model: string;
  provider: string;
  finishReason?: string;
  /** Optional usage counts when the provider returns them. */
  usage?: { promptTokens?: number; completionTokens?: number };
}

export interface AgentProvider {
  readonly name: string;
  complete(opts: CompletionOptions): Promise<CompletionResult>;
}

export class OfflineProvider implements AgentProvider {
  public readonly name = "offline";
  constructor(private readonly model: string = "animica-offline-v1") {}
  async complete(opts: CompletionOptions): Promise<CompletionResult> {
    const lastUser = [...opts.messages].reverse().find((m) => m.role === "user");
    const prompt = lastUser?.content ?? "";
    const synthesized = synthesizePlan(prompt);
    return {
      text: synthesized,
      model: opts.model ?? this.model,
      provider: this.name,
      finishReason: "stop",
    };
  }
}

/**
 * OpenAI-compatible chat completions endpoint. Anthropic users should point
 * `providerBaseUrl` at an OpenAI-compatible gateway, or use the
 * `AnthropicProvider` below directly.
 */
export class OpenAICompatibleProvider implements AgentProvider {
  public readonly name: string;
  constructor(
    private readonly baseUrl: string,
    private readonly apiKeyEnv: string,
    private readonly defaultModel: string,
    name = "openai-compatible",
  ) {
    this.name = name;
  }
  async complete(opts: CompletionOptions): Promise<CompletionResult> {
    const key = process.env[this.apiKeyEnv];
    if (!key) throw new AgentError("PROVIDER", `missing env ${this.apiKeyEnv}`);
    const body = {
      model: opts.model ?? this.defaultModel,
      messages: opts.messages,
      temperature: opts.temperature ?? 0.2,
      max_tokens: opts.maxTokens ?? 1024,
    };
    const res = await fetch(`${this.baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${key}` },
      body: JSON.stringify(body),
      signal: opts.signal,
    });
    if (!res.ok) {
      throw new AgentError("PROVIDER", `HTTP ${res.status}: ${await safeText(res)}`);
    }
    const j = (await res.json()) as {
      choices: { message: { content: string }; finish_reason?: string }[];
      usage?: { prompt_tokens?: number; completion_tokens?: number };
      model?: string;
    };
    const choice = j.choices?.[0];
    return {
      text: choice?.message?.content ?? "",
      model: j.model ?? body.model,
      provider: this.name,
      finishReason: choice?.finish_reason,
      usage: j.usage
        ? { promptTokens: j.usage.prompt_tokens, completionTokens: j.usage.completion_tokens }
        : undefined,
    };
  }
}

export class AnthropicProvider implements AgentProvider {
  public readonly name = "anthropic";
  constructor(
    private readonly baseUrl: string = "https://api.anthropic.com",
    private readonly defaultModel: string = "claude-opus-4-7",
  ) {}
  async complete(opts: CompletionOptions): Promise<CompletionResult> {
    const key = process.env.ANTHROPIC_API_KEY;
    if (!key) throw new AgentError("PROVIDER", "missing env ANTHROPIC_API_KEY");
    const sys = opts.messages.find((m) => m.role === "system")?.content;
    const turns = opts.messages.filter((m) => m.role !== "system");
    const body = {
      model: opts.model ?? this.defaultModel,
      system: sys,
      messages: turns.map((m) => ({ role: m.role, content: m.content })),
      max_tokens: opts.maxTokens ?? 1024,
      temperature: opts.temperature ?? 0.2,
    };
    const res = await fetch(`${this.baseUrl}/v1/messages`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify(body),
      signal: opts.signal,
    });
    if (!res.ok) throw new AgentError("PROVIDER", `HTTP ${res.status}: ${await safeText(res)}`);
    const j = (await res.json()) as {
      content?: { type: string; text?: string }[];
      model?: string;
      stop_reason?: string;
      usage?: { input_tokens?: number; output_tokens?: number };
    };
    const text = (j.content ?? [])
      .filter((c) => c.type === "text" && typeof c.text === "string")
      .map((c) => c.text!)
      .join("");
    return {
      text,
      model: j.model ?? body.model,
      provider: this.name,
      finishReason: j.stop_reason,
      usage: j.usage
        ? { promptTokens: j.usage.input_tokens, completionTokens: j.usage.output_tokens }
        : undefined,
    };
  }
}

async function safeText(res: Response): Promise<string> {
  try {
    return (await res.text()).slice(0, 512);
  } catch {
    return "<unreadable body>";
  }
}

/** Build a provider from config + env. */
export function pickProvider(cfg: AgentConfig): AgentProvider {
  const explicit = (cfg.provider ?? "").toLowerCase();
  if (explicit === "anthropic") {
    return new AnthropicProvider(cfg.providerBaseUrl ?? "https://api.anthropic.com", cfg.defaultModel);
  }
  if (explicit === "openai") {
    return new OpenAICompatibleProvider(
      cfg.providerBaseUrl ?? "https://api.openai.com",
      "OPENAI_API_KEY",
      cfg.defaultModel || "gpt-4o-mini",
      "openai",
    );
  }
  if (explicit === "aicf" && cfg.providerBaseUrl) {
    return new OpenAICompatibleProvider(
      cfg.providerBaseUrl,
      "ANIMICA_AICF_KEY",
      cfg.defaultModel || "animica-aicf-v1",
      "aicf",
    );
  }
  return new OfflineProvider(cfg.defaultModel);
}

/** Deterministic offline plan: useful for tests, docs, and demoing without a network. */
function synthesizePlan(prompt: string): string {
  const p = prompt.trim();
  if (!p) {
    return "No task provided. Type `animica-agent help` or describe what you want.";
  }
  const lines: string[] = [];
  lines.push("# Plan (offline provider)");
  lines.push("");
  lines.push(`The offline provider is active; no remote model is being called. The task understood is:`);
  lines.push(`> ${p.slice(0, 400)}`);
  lines.push("");
  lines.push("Suggested next steps:");
  lines.push("1. Configure a real provider with `ANIMICA_AGENT_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=…`,");
  lines.push("   or `ANIMICA_AGENT_PROVIDER=aicf` with `ANIMICA_AGENT_PROVIDER_BASE_URL` + `ANIMICA_AICF_KEY`.");
  lines.push("2. Re-run `animica-agent code \"<task>\"` to receive a real plan and patch.");
  lines.push("3. Use `animica-agent contract|dapp|token scaffold` for deterministic, offline-safe scaffolding.");
  return lines.join("\n");
}
