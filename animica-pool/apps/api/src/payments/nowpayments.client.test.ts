import { describe, it, expect, beforeAll } from "vitest";
import { createHmac } from "node:crypto";

const SECRET = "test-ipn-secret";

function sortDeep(v: any): any {
  if (Array.isArray(v)) return v.map(sortDeep);
  if (v && typeof v === "object") {
    return Object.keys(v).sort().reduce((a: any, k) => ((a[k] = sortDeep(v[k])), a), {});
  }
  return v;
}
function sign(body: unknown): string {
  return createHmac("sha512", SECRET).update(JSON.stringify(sortDeep(body))).digest("hex");
}

let verifyIpnSignature: (body: unknown, sig: string | null) => boolean;

beforeAll(async () => {
  process.env.NOWPAYMENTS_IPN_SECRET = SECRET;
  process.env.JWT_SECRET = "x".repeat(32);
  process.env.DATABASE_URL = "postgresql://x:y@localhost:5439/db";
  ({ verifyIpnSignature } = await import("./nowpayments.client"));
});

describe("NOWPayments IPN verification", () => {
  const body = { payment_id: "123", payment_status: "finished", order_id: "ord_1", price_amount: 50 };

  it("accepts a correctly-signed payload (key order independent)", () => {
    const shuffled = { order_id: "ord_1", price_amount: 50, payment_status: "finished", payment_id: "123" };
    expect(verifyIpnSignature(shuffled, sign(body))).toBe(true);
  });

  it("rejects a tampered payload", () => {
    expect(verifyIpnSignature({ ...body, price_amount: 5000 }, sign(body))).toBe(false);
  });

  it("rejects a missing/blank signature", () => {
    expect(verifyIpnSignature(body, null)).toBe(false);
    expect(verifyIpnSignature(body, "")).toBe(false);
  });
});
