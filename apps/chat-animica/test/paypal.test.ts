import { describe, expect, it } from "vitest";
import { parseSubscriptionWebhook } from "../src/server/paypal/client";

describe("parseSubscriptionWebhook", () => {
  it("extracts normalized subscription payload", () => {
    const payload = parseSubscriptionWebhook({
      resource: {
        id: "I-SUB123",
        status: "ACTIVE",
        billing_info: { next_billing_time: "2030-01-01T00:00:00Z" },
        subscriber: { email_address: "user@example.com", payer_id: "PAYER" }
      }
    });

    expect(payload).toEqual({
      subscriptionId: "I-SUB123",
      status: "ACTIVE",
      nextBillingTime: "2030-01-01T00:00:00Z",
      payerEmail: "user@example.com",
      payerId: "PAYER"
    });
  });

  it("returns null when payload missing resource id", () => {
    expect(parseSubscriptionWebhook({ resource: {} })).toBeNull();
  });
});
