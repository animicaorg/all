import { z } from "zod";

const schema = z.object({
  DATABASE_URL: z.string().min(1),
  REDIS_URL: z.string().min(1),
  JWT_SECRET: z.string().min(16),
  SMTP_HOST: z.string().optional(),
  SMTP_PORT: z.coerce.number().optional(),
  SMTP_USER: z.string().optional(),
  SMTP_PASS: z.string().optional(),
  SMTP_FROM: z.string().optional(),
  PAYPAL_CLIENT_ID: z.string().optional(),
  PAYPAL_SECRET: z.string().optional(),
  PAYPAL_WEBHOOK_ID: z.string().optional(),
  PAYPAL_PLAN_ID: z.string().optional(),
  PAYPAL_BASE_URL: z.string().default("https://api-m.sandbox.paypal.com"),
  MODAL_CHAT_URL: z.string().url().optional(),
  ANIMICA_RPC_URL: z.string().default("https://mainnet.animica.org/rpc"),
  EXPLORER_TX_URL: z.string().default("https://explorer.animica.org/tx/{hash}"),
  DEV_SIGNER_KEY: z.string().optional()
});

export const env = schema.parse(process.env);
