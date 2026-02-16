import crypto from "crypto";
import { env } from "@/src/server/env";

export function canUseDevSigner() {
  return Boolean(env.DEV_SIGNER_KEY);
}

export function signWithDevSigner(txDraft: unknown) {
  if (!env.DEV_SIGNER_KEY) throw new Error("DEV_SIGNER_KEY not enabled");
  const digest = crypto.createHmac("sha256", env.DEV_SIGNER_KEY).update(JSON.stringify(txDraft)).digest("hex");
  return `0x${digest}`;
}
