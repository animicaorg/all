import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DEFAULT_CONFIG } from "../src/config.js";
import { detectMinerIdentity, evaluateEligibility, planResources, resolveMinerMode } from "../src/miner.js";

describe("miner", () => {
  let env: NodeJS.ProcessEnv;
  beforeEach(() => {
    env = { ...process.env };
    delete process.env.ANIMICA_POOL_ADDRESS;
    delete process.env.ANIMICA_POOL_URL;
    delete process.env.ANIMICA_POOL_MODE;
    delete process.env.ANIMICA_MINER_STRATUM_ENABLED;
  });
  afterEach(() => {
    process.env = env;
  });

  it("returns 'none' when nothing is configured", () => {
    const id = detectMinerIdentity({ ...DEFAULT_CONFIG, minerAddress: undefined });
    expect(id.source).toBe("none");
  });

  it("picks up payout address from ANIMICA_POOL_ADDRESS", () => {
    process.env.ANIMICA_POOL_ADDRESS = "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l";
    const id = detectMinerIdentity({ ...DEFAULT_CONFIG, minerAddress: undefined });
    expect(id.source).toBe("env");
    expect(id.payoutAddress).toBe("anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l");
  });

  it("prefers a user-supplied minerAddress over env", () => {
    process.env.ANIMICA_POOL_ADDRESS = "anm1qpzry9x8gf2tvdw0s3jn54khce6envxx";
    const id = detectMinerIdentity({ ...DEFAULT_CONFIG, minerAddress: "anm1qpzry9x8gf2tvdw0s3jn54khce6usrxxx" });
    expect(id.source).toBe("user");
    expect(id.payoutAddress).toBe("anm1qpzry9x8gf2tvdw0s3jn54khce6usrxxx");
  });

  it("eligibility honors creditsMode policies", () => {
    const cfg = { ...DEFAULT_CONFIG, creditsMode: "miner" as const };
    const id = detectMinerIdentity({ ...cfg, minerAddress: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l" });
    const r = evaluateEligibility(cfg, id);
    expect(r.allowed).toBe(true);
    expect(r.minerConnected).toBe(true);
  });

  it("resourceMode miner-priority lowers cpu when miner is hot", () => {
    const plan = planResources({ ...DEFAULT_CONFIG, resourceMode: "miner-priority" }, true);
    expect(plan.cpuLimitPercent).toBeLessThanOrEqual(25);
    expect(plan.backgroundOnly).toBe(true);
  });

  it("resolveMinerMode auto picks 'local' when miner is running", () => {
    process.env.ANIMICA_MINER_STRATUM_ENABLED = "1";
    expect(resolveMinerMode("auto")).toBe("local");
  });
});
