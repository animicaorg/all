import { describe, expect, it } from "vitest";

import { classifyTxSendFailure, NodeWalletSigner, parseTxHash, SignerError } from "../src/signers.js";

describe("classifyTxSendFailure", () => {
  it.each([
    ["error: insufficient balance for transfer", "insufficient-balance"],
    ["insufficient funds (have 0 nANM, need 100 nANM)", "insufficient-balance"],
    ["chainId mismatch: expected 1 got 42", "bad-chain-id"],
    ["wallet not found: anm1xyz", "wallet-not-found"],
    ["keystore not found at .animica/keys", "wallet-not-found"],
    ["nonce too low (got 4, expected 5)", "nonce-conflict"],
    ["invalid nonce", "nonce-conflict"],
    ["ECONNREFUSED connecting to RPC at 127.0.0.1:8545", "rpc-unavailable"],
    ["RPC unavailable", "rpc-unavailable"],
    ["not admitted: mempool full", "tx-not-admitted"],
    ["tx rejected: signature invalid", "tx-rejected"],
    ["wait-timeout: did not confirm in 60s", "tx-not-confirmed"],
    ["something completely different", "unknown"],
  ])("classifies %j as %s", (msg, expected) => {
    expect(classifyTxSendFailure(msg)).toBe(expected);
  });
});

describe("parseTxHash", () => {
  it("parses txHash=0x… form", () => {
    expect(parseTxHash("ok\ntxHash=0xABCDEF1234567890123456789012345678901234567890\nok")).toBe(
      "0xabcdef1234567890123456789012345678901234567890",
    );
  });
  it("parses JSON object form", () => {
    expect(parseTxHash('{"txHash": "0x1234567890abcdef1234567890abcdef12345678"}')).toBe(
      "0x1234567890abcdef1234567890abcdef12345678",
    );
  });
  it("parses bare hash on its own line", () => {
    expect(parseTxHash("submitting...\n0x1234567890abcdef1234567890abcdef12345678\n")).toBe(
      "0x1234567890abcdef1234567890abcdef12345678",
    );
  });
  it("returns undefined when no hash is present", () => {
    expect(parseTxHash("submission timed out")).toBeUndefined();
  });
});

describe("NodeWalletSigner with shim", () => {
  it("returns the parsed txHash on success", async () => {
    const signer = new NodeWalletSigner({
      spawn: () => ({
        status: 0,
        stdout: "submitting...\ntxHash=0xabcdef1234567890123456789012345678901234\n",
        stderr: "",
      }),
    });
    const r = await signer.sign({
      payload: { kind: "anm-transfer", data: { from: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l", to: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l", valueRaw: 1n } },
      reason: "test",
      estimatedCostRaw: 1n,
    });
    expect(r.txHash).toMatch(/^0xabcdef/);
  });

  it("surfaces insufficient-balance as SignerError", async () => {
    const signer = new NodeWalletSigner({
      spawn: () => ({ status: 1, stdout: "", stderr: "error: insufficient balance" }),
    });
    await expect(
      signer.sign({
        payload: { kind: "anm-transfer", data: { from: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l", to: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l", valueRaw: 1n } },
        reason: "test",
        estimatedCostRaw: 1n,
      }),
    ).rejects.toMatchObject({ name: "SignerError", reason: "insufficient-balance" });
  });

  it("does not fabricate success when exit=0 but no hash is found", async () => {
    const signer = new NodeWalletSigner({
      spawn: () => ({ status: 0, stdout: "all good!", stderr: "" }),
    });
    await expect(
      signer.sign({
        payload: { kind: "anm-transfer", data: { from: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l", to: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l", valueRaw: 1n } },
        reason: "test",
        estimatedCostRaw: 1n,
      }),
    ).rejects.toMatchObject({ name: "SignerError", reason: "tx-not-admitted" });
  });
});
