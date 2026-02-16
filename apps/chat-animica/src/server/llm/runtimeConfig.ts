import fs from "node:fs";
import path from "node:path";
import { env } from "@/src/server/env";

const ENDPOINT_FILE = path.join(process.cwd(), ".modal-endpoint");

function readEndpointFile(): string | undefined {
  try {
    const value = fs.readFileSync(ENDPOINT_FILE, "utf8").trim();
    return value || undefined;
  } catch {
    return undefined;
  }
}

function isValidHttpsUrl(url: string | undefined): url is string {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:";
  } catch {
    return false;
  }
}

let warned = false;

export type LlmRuntimeConfig = {
  endpointUrl?: string;
  fallbackReason?: string;
  usingFallback: boolean;
  model: string;
  maxTokens: number;
  temperature: number;
};

export function getLlmRuntimeConfig(): LlmRuntimeConfig {
  const envEndpoint = env.MODAL_ENDPOINT_URL?.trim();
  const fileEndpoint = readEndpointFile();
  const endpointUrl = isValidHttpsUrl(envEndpoint)
    ? envEndpoint
    : isValidHttpsUrl(fileEndpoint)
      ? fileEndpoint
      : undefined;

  const hasCreds = Boolean(env.MODAL_TOKEN_ID && env.MODAL_TOKEN_SECRET);
  if (!endpointUrl && !warned) {
    warned = true;
    const reason = hasCreds
      ? "Modal endpoint missing. Run `pnpm --filter chat-animica modal:deploy` or restart dev to auto-bootstrap."
      : "Modal creds missing (MODAL_TOKEN_ID / MODAL_TOKEN_SECRET). Using local LLM fallback.";
    console.warn(`[llm-config] ${reason}`);
  }

  return {
    endpointUrl,
    usingFallback: !endpointUrl,
    fallbackReason: !endpointUrl
      ? hasCreds
        ? "missing_endpoint"
        : "missing_modal_credentials"
      : undefined,
    model: env.LLM_MODEL,
    maxTokens: env.LLM_MAX_TOKENS,
    temperature: env.LLM_TEMPERATURE
  };
}
