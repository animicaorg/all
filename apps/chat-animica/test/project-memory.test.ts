import { beforeEach, describe, expect, it } from "vitest";

beforeEach(() => {
  process.env.DATABASE_URL = process.env.DATABASE_URL ?? "postgresql://localhost:5432/dev";
  process.env.REDIS_URL = process.env.REDIS_URL ?? "redis://localhost:6379";
  process.env.JWT_SECRET = process.env.JWT_SECRET ?? "test-jwt-secret-123456";
  process.env.PROJECT_MEMORY_FILE = ".data/test-project-memory.json";
});

describe("project memory persistence", () => {
  it("stores and caps revisions at 10", async () => {
    const mod = await import("../src/server/project/userState");
    for (let i = 0; i < 12; i += 1) {
      await mod.saveProjectMemory("u1", `rev-${i}`);
    }
    const state = await mod.getUserState("u1");
    expect(state.memory.revisions).toHaveLength(10);
    expect(state.memory.latest).toBe("rev-11");
  });
});
