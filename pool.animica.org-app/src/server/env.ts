// Centralised env loader + validation for the rig-rental marketplace.
//
// Imported once at server start; throws a clear error if anything required
// is missing in prod, but tolerates missing values during development (a
// noisy `console.warn` in that case). Same pattern as buy.animica.org.

import { z } from "zod";

const Schema = z.object({
  NODE_ENV: z.enum(["development", "production", "test"]).default("development"),
  PUBLIC_BASE_URL: z.string().url().default("http://localhost:3020/app"),
  PORT: z.string().default("3020"),

  SESSION_SECRET: z.string().min(32),
  COOKIE_DOMAIN: z.string().optional().default(""),

  DATABASE_URL: z.string().url(),

  // NOWPayments (pay-in invoices + auto-converted payouts to owners/renters)
  NOWPAYMENTS_API_KEY: z.string().min(1),
  NOWPAYMENTS_PUBLIC_KEY: z.string().optional().default(""),
  NOWPAYMENTS_IPN_SECRET: z.string().min(1),
  NOWPAYMENTS_EMAIL: z.string().optional().default(""),
  NOWPAYMENTS_PASSWORD: z.string().optional().default(""),
  NOWPAYMENTS_PUBLIC_CALLBACK_URL: z.string().url(),
  NOWPAYMENTS_SUCCESS_URL: z.string().url(),
  NOWPAYMENTS_CANCEL_URL: z.string().url(),
  NOWPAYMENTS_SANDBOX: z
    .string()
    .optional()
    .default("false")
    .transform((v) => v === "true"),

  // Magic-link email (Gmail SMTP from animicaorg@gmail.com, shared with chat)
  MAIL_DRIVER: z.enum(["console", "smtp"]).default("console"),
  MAIL_FROM: z.string().default("Animica Rig Rental <animicaorg@gmail.com>"),
  SMTP_HOST: z.string().optional().default(""),
  SMTP_PORT: z.coerce.number().int().positive().default(587),
  SMTP_USER: z.string().optional().default(""),
  SMTP_PASS: z.string().optional().default(""),

  // Pool API (server-side only; HMAC-signed rental calls)
  POOL_API_BASE_URL: z.string().url().default("http://127.0.0.1:8550"),
  POOL_RENTAL_SHARED_SECRET: z.string().min(16),

  // Marketplace economics
  MARKETPLACE_FEE_PERCENT: z.string().default("5"),
  RIG_OFFLINE_GRACE_SECONDS: z.coerce.number().int().positive().default(600),
  QUOTE_TTL_SECONDS: z.coerce.number().int().positive().default(600),

  ADMIN_EMAILS: z.string().optional().default(""),

  // AI compute broker
  AI_BROKER_ENABLED: z
    .string()
    .optional()
    .default("true")
    .transform((v) => v !== "false"),
  // Auto-grant on first key mint so dev/demo keys can call the API immediately.
  DEFAULT_CREDIT_GRANT_USD: z.string().optional().default("1.00"),
  PROVIDER_TIMEOUT_MS: z.coerce.number().int().positive().default(30000),
});

export type Env = z.infer<typeof Schema>;

let _cached: Env | null = null;

export function env(): Env {
  if (_cached) return _cached;
  const parsed = Schema.safeParse(process.env);
  if (!parsed.success) {
    const issues = parsed.error.issues
      .map((i) => `${i.path.join(".")}: ${i.message}`)
      .join("\n  ");
    if (process.env.NODE_ENV === "production") {
      throw new Error(`Invalid env configuration:\n  ${issues}`);
    }
    // eslint-disable-next-line no-console
    console.warn(`[env] running with incomplete configuration:\n  ${issues}`);
    _cached = Schema.partial().parse(process.env) as Env;
    return _cached;
  }
  _cached = parsed.data;
  return _cached;
}
