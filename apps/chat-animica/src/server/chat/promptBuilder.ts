export type ChatMode = "strict" | "possibility";

export function buildPrompt(input: { prompt: string; mode: ChatMode; projectMemory?: string }) {
  const modeInstruction = input.mode === "strict"
    ? "Produce deploy-ready contract artifacts only. No commentary."
    : "Explore alternatives, then emit best contract with notes.";

  return [
    "You are Animica contract generator.",
    `Mode: ${input.mode}`,
    modeInstruction,
    input.projectMemory ? `Project memory:\n${input.projectMemory}` : "",
    `User request:\n${input.prompt}`,
    "Return JSON with fields: content, abi, manifest"
  ].filter(Boolean).join("\n\n");
}
