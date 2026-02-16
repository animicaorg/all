import { canUseDevSigner, signWithDevSigner } from "@/src/server/wallet/devSigner";
import { env } from "@/src/server/env";
import { prisma } from "@/src/server/db/prisma";

type ResolveInput = {
  signerType?: "extension" | "wallet" | "dev";
  signedRawTx?: string;
  rawTx?: string;
  txCbor: string;
  userId: string;
};

export async function resolveRawTransaction(input: ResolveInput): Promise<{ rawTx?: string; error?: string; mode: string }> {
  if (input.signerType === "extension") {
    const extRawTx = input.signedRawTx ?? input.rawTx;
    if (!extRawTx) return { mode: "extension", error: "Extension signer selected but no signed transaction payload provided." };
    return { mode: "extension", rawTx: extRawTx };
  }

  if (input.signerType === "wallet") {
    const session = await prisma.walletSession.findFirst({
      where: { userId: input.userId, status: "active" },
      orderBy: { lastUsedAt: "desc" }
    });
    if (!session) return { mode: "wallet", error: "No wallet session found. Connect wallet first." };
    if (env.ENABLE_WALLET_PROD_SIGNING !== "1") {
      return { mode: "wallet", error: "Wallet signing is disabled by feature flag ENABLE_WALLET_PROD_SIGNING=0." };
    }
    return { mode: "wallet", error: "Wallet signer provider must be integrated with wallet session token." };
  }

  if (input.signerType === "dev" || (!input.signerType && canUseDevSigner())) {
    if (!canUseDevSigner()) return { mode: "dev", error: "DEV_SIGNER_KEY is not configured." };
    return { mode: "dev", rawTx: signWithDevSigner(input.txCbor) };
  }

  return { mode: "none", error: "No signer available. Use extension or enable dev signer." };
}
