import crypto from "crypto";
import { env } from "@/src/server/env";

export function canUseDevSigner() {
  return Boolean(env.DEV_SIGNER_KEY);
}

export function signWithDevSigner(rawPayloadHex: string) {
  if (!env.DEV_SIGNER_KEY) throw new Error("DEV_SIGNER_KEY not enabled");
  const payload = rawPayloadHex.startsWith("0x") ? rawPayloadHex.slice(2) : rawPayloadHex;
  const digest = crypto.createHmac("sha256", env.DEV_SIGNER_KEY).update(payload).digest("hex");
  return `0x${payload}${digest}`;
}
