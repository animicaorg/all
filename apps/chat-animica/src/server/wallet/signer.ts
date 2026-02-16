import { canUseDevSigner, signWithDevSigner } from "@/src/server/wallet/devSigner";
import { env } from "@/src/server/env";
import { prisma } from "@/src/server/db/prisma";

type ResolveInput = {
  signerType?: "extension" | "wallet" | "dev";
  signedRawTx?: string;
  rawTx?: string;
  txDraft?: Record<string, unknown>;
  userId: string;
};

export async function resolveRawTransaction(input: ResolveInput): Promise<{ rawTx?: string; error?: string }> {
  if (input.signerType === "extension") {
    const extRawTx = input.signedRawTx ?? input.rawTx;
    if (!extRawTx) return { error: "Extension signer selected but no signedRawTx provided." };
    return { rawTx: extRawTx };
  }

  if (input.signerType === "wallet") {
    const session = await prisma.walletSession.findFirst({
      where: { userId: input.userId, status: "active" },
      orderBy: { lastUsedAt: "desc" }
    });
    if (!session) return { error: "Animica Wallet session not found. Please connect mobile wallet first." };
    if (env.WALLET_MOCK === "1" && input.txDraft) {
      return { rawTx: signWithDevSigner({ walletMock: true, txDraft: input.txDraft, sessionId: session.id }) };
    }
    return { error: "WalletSessionSigner not implemented yet for production signing." };
  }

  if (input.signerType === "dev" || (!input.signerType && canUseDevSigner() && input.txDraft)) {
    if (!input.txDraft) return { error: "Dev signer requires txDraft." };
    if (!canUseDevSigner()) return { error: "DEV_SIGNER_KEY not enabled." };
    return { rawTx: signWithDevSigner(input.txDraft) };
  }

  if (input.signedRawTx ?? input.rawTx) {
    return { rawTx: input.signedRawTx ?? input.rawTx };
  }

  return { error: "No signer available. Connect extension/mobile wallet or enable Dev signer." };
}
