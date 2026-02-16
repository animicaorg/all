import { beforeAll, describe, expect, it } from "vitest";

let wallet: Awaited<typeof import("../src/server/wallet/connect")>;

beforeAll(async () => {
  process.env.DATABASE_URL = process.env.DATABASE_URL ?? "postgresql://localhost:5432/dev";
  process.env.REDIS_URL = process.env.REDIS_URL ?? "redis://localhost:6379";
  process.env.JWT_SECRET = process.env.JWT_SECRET ?? "test-jwt-secret-123456";
  process.env.WALLET_CONNECT_SIGNING_KEY = process.env.WALLET_CONNECT_SIGNING_KEY ?? "test-wallet-signing-key-123456";
  wallet = await import("../src/server/wallet/connect");
});

describe("wallet connect signatures", () => {
  const payload = {
    v: 1 as const,
    app: "Animica Studio" as const,
    origin: "https://studio.animica.org",
    nonce: "abc123",
    ts: 1730000000,
    callback: "https://studio.animica.org/api/wallet/callback",
    scopes: ["accounts", "signTx"] as const,
    chainId: 1
  };

  it("signs and verifies payload", () => {
    const signature = wallet.signConnectPayload(payload);
    expect(wallet.verifyConnectPayload(payload, signature)).toBe(true);
    expect(wallet.verifyConnectPayload({ ...payload, nonce: "changed" }, signature)).toBe(false);
  });

  it("signs and verifies nonce", () => {
    const signature = wallet.signNonce(payload.nonce);
    expect(wallet.verifyNonceSignature(payload.nonce, signature)).toBe(true);
    expect(wallet.verifyNonceSignature("bad", signature)).toBe(false);
  });

  it("encodes request bundle", () => {
    const signature = wallet.signConnectPayload(payload);
    const encoded = wallet.encodeRequest(payload, signature);
    expect(encoded.length).toBeGreaterThan(24);
  });
});
