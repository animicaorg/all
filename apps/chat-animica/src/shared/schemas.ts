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
  signedRawTx: z.string().startsWith("0x").optional()
});
