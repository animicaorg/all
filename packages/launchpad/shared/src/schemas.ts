import { z } from "zod";
import {
  ACTIVITY_TYPES,
  PROJECT_STATUSES,
  RISK_LEVELS,
  WALLET_TYPES
} from "./constants";
import { isAnimicaAddress } from "./address";

// Animica addresses are bech32m with HRP="anim" — alg_id(2B) || sha3_256(pubkey)(32B).
// The full checksum is verified here, not just a charset check.
export const animicaAddressSchema = z
  .string()
  .trim()
  .transform((s) => s.toLowerCase())
  .refine(isAnimicaAddress, "Expected an Animica bech32m address (anim1…)");

export const walletTypeSchema = z.enum(WALLET_TYPES);
export const projectStatusSchema = z.enum(PROJECT_STATUSES);
export const riskLevelSchema = z.enum(RISK_LEVELS);
export const activityTypeSchema = z.enum(ACTIVITY_TYPES);

export const projectCreateSchema = z.object({
  name: z.string().trim().min(2).max(60),
  symbol: z
    .string()
    .trim()
    .min(2)
    .max(10)
    .regex(/^[A-Z0-9]+$/, "Use uppercase letters and digits"),
  description: z.string().trim().min(10).max(800),
  logoUrl: z.string().url().optional().or(z.literal("")),
  bannerUrl: z.string().url().optional().or(z.literal("")),
  websiteUrl: z.string().url().optional().or(z.literal("")),
  twitterUrl: z.string().url().optional().or(z.literal("")),
  telegramUrl: z.string().url().optional().or(z.literal("")),
  discordUrl: z.string().url().optional().or(z.literal("")),
  githubUrl: z.string().url().optional().or(z.literal("")),
  docsUrl: z.string().url().optional().or(z.literal("")),
  initialSupply: z
    .string()
    .regex(/^\d+(\.\d+)?$/, "Numeric supply expected")
    .optional()
    .or(z.literal("")),
  categorySlug: z.string().trim().min(2).max(40).optional(),
  tags: z.array(z.string().trim().min(1).max(24)).max(6).optional(),
  memo: z.string().max(400).optional(),
  creatorWallet: animicaAddressSchema.optional()
});
export type ProjectCreateInput = z.infer<typeof projectCreateSchema>;

export const commentCreateSchema = z.object({
  body: z.string().trim().min(1).max(800),
  parentId: z.string().optional()
});

export const reportCreateSchema = z.object({
  reason: z.enum([
    "SPAM",
    "SCAM",
    "IMPERSONATION",
    "ILLEGAL",
    "MISLEADING",
    "OTHER"
  ]),
  details: z.string().trim().max(800).optional()
});

export const nonceRequestSchema = z.object({
  address: animicaAddressSchema,
  walletType: walletTypeSchema
});

export const verifySignatureSchema = z.object({
  address: animicaAddressSchema,
  walletType: walletTypeSchema,
  nonce: z.string().min(8),
  signature: z.string().min(8),
  publicKey: z.string().regex(/^0x[0-9a-fA-F]{64,}$/, "publicKey must be 0x-prefixed hex"),
  algId: z.number().int().min(0).max(0xffff),
  algName: z.string().min(2).max(40).optional(),
  chainId: z.number().int().nonnegative().optional()
});

export const quoteRequestSchema = z
  .object({
    side: z.enum(["BUY", "SELL"]),
    amountInAnm: z.string().regex(/^\d+(\.\d+)?$/).optional(),
    amountInToken: z.string().regex(/^\d+(\.\d+)?$/).optional(),
    slippageBps: z.number().int().min(0).max(5000).optional()
  })
  .refine((v: { amountInAnm?: string; amountInToken?: string }) => Boolean(v.amountInAnm || v.amountInToken), {
    message: "Provide amountInAnm or amountInToken"
  });

export const tradeExecuteSchema = z.object({
  quoteId: z.string().optional(),
  side: z.enum(["BUY", "SELL"]),
  amountInAnm: z.string().regex(/^\d+(\.\d+)?$/).optional(),
  amountInToken: z.string().regex(/^\d+(\.\d+)?$/).optional(),
  slippageBps: z.number().int().min(0).max(5000).optional(),
  txHash: z.string().optional()
});

export const adminActionSchema = z.object({
  reason: z.string().trim().max(400).optional()
});

export const adminRiskSchema = z.object({
  riskLevel: riskLevelSchema,
  notes: z.string().trim().max(800).optional()
});

export const adminSettingSchema = z.object({
  key: z.string().trim().min(2).max(60),
  value: z.string().max(10_000)
});
