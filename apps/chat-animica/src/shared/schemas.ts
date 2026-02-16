import { z } from "zod";

export const requestLinkSchema = z.object({
  email: z.string().email()
});

export const chatSchema = z.object({
  prompt: z.string().min(1),
  threadId: z.string().optional(),
  projectId: z.string().optional()
});

export const deploySchema = z.object({
  contractId: z.string(),
  rawTx: z.string().startsWith("0x").optional(),
  txDraft: z.record(z.any()).optional(),
  signedRawTx: z.string().startsWith("0x").optional(),
  signerType: z.enum(["extension", "wallet", "dev"]).optional()
});

export const walletConnectStartSchema = z.object({
  chainId: z.number().int().positive().default(1)
});

export const walletCallbackSchema = z.object({
  requestId: z.string(),
  approved: z.boolean(),
  accounts: z.array(z.string()).optional(),
  sessionPublicKey: z.string().optional(),
  sessionToken: z.string().optional(),
  nonceSignature: z.string()
});
