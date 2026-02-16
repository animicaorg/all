import { describe, expect, it } from "vitest";
import { buildPrompt } from "../src/server/chat/promptBuilder";

describe("buildPrompt", () => {
  it("includes Strict Animica Guardrails in every prompt", () => {
    const prompt = buildPrompt({ prompt: "deploy token", mode: "strict" });
    expect(prompt).toContain("Strict Animica Guardrails");
    expect(prompt).toContain("Never invent APIs");
    expect(prompt).toContain("tx.sendRawTransaction / tx.submitRawTransaction");
  });
});
