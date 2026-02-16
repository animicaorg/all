import { env } from "@/src/server/env";

export async function callModalChat(input: { prompt: string; context?: unknown }) {
  if (!env.MODAL_CHAT_URL) {
    return {
      content: "// MODAL_CHAT_URL not configured; returning deterministic stub contract\ncontract Hello { fn greet() -> string { return 'hi'; } }",
      abi: [{ name: "greet", type: "function" }],
      manifest: { language: "animica" }
    };
  }

  const res = await fetch(env.MODAL_CHAT_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input)
  });

  if (!res.ok) {
    throw new Error(`Modal chat request failed: ${res.status}`);
  }

  return res.json();
}
