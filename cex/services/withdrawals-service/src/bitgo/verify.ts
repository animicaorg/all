/**
 * BitGo Webhook Signature Verification
 */

import crypto from "crypto";

export function verifyWebhookSignature(
  payload: string | Buffer,
  signature: string,
  secret: string
): boolean {
  try {
    const hmac = crypto.createHmac("sha256", secret);
    hmac.update(payload);
    const computedSignature = hmac.digest("hex");
    
    // Use timing-safe comparison
    return crypto.timingSafeEqual(
      Buffer.from(signature),
      Buffer.from(computedSignature)
    );
  } catch (error) {
    return false;
  }
}
