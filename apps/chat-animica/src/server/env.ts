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
  MODAL_TOKEN_ID: z.string().optional(),
  MODAL_TOKEN_SECRET: z.string().optional(),
  MODAL_ENV: z.string().default("dev"),
  MODAL_APP_NAME: z.string().default("chat-animica-llm"),
  MODAL_REGION: z.string().optional(),
  MODAL_ENDPOINT_URL: z.string().url().optional(),
  LLM_MODEL: z.string().default("gpt-4o-mini"),
  LLM_MAX_TOKENS: z.coerce.number().int().positive().default(1200),
  LLM_TEMPERATURE: z.coerce.number().min(0).max(2).default(0.2),
  ANIMICA_RPC_URL: z.string().url().default("https://mainnet.animica.org/rpc"),
  EXPLORER_TX_URL: z.string().default("https://explorer.animica.org/tx/{hash}"),
  DEV_SIGNER_KEY: z.string().optional(),
  WALLET_CONNECT_SIGNING_KEY: z.string().min(16).default("dev-wallet-signing-key-change-me"),
  WALLET_CONNECT_CALLBACK_URL: z.string().url().optional(),
  NEXT_PUBLIC_APP_ORIGIN: z.string().url().optional(),
  NEXT_PUBLIC_DEFAULT_CHAIN_ID: z.coerce.number().int().positive().default(1),
  ENABLE_WALLET_PROD_SIGNING: z.enum(["0", "1"]).default("0"),
  PROJECT_MEMORY_FILE: z.string().default(".data/project-memory.json"),
  KNOWLEDGE_PACK_FILE: z.string().default(".data/knowledge-pack.json")
});

export const env = schema.parse(process.env);
