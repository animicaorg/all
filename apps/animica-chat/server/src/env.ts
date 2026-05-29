// Read + validate environment variables once at startup. Failing fast
// here is far better than throwing in a request handler at 3am.

import { z } from 'zod';
import dotenv from 'dotenv';
import path from 'path';

dotenv.config({ path: path.resolve(__dirname, '../../.env') });

const Env = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  PORT: z.coerce.number().int().positive().default(4400),
  PUBLIC_BASE_URL: z.string().url().default('http://localhost:4400'),
  CORS_ORIGINS: z.string().default('http://localhost:5173'),
  COOKIE_DOMAIN: z.string().optional().default(''),
  JWT_SECRET: z
    .string()
    .min(16, 'JWT_SECRET must be at least 16 chars (use `openssl rand -hex 32`)'),

  DATABASE_URL: z.string().min(1, 'DATABASE_URL is required'),

  // AI provider (OpenAI-compatible by default; abstraction lives in
  // services/aiProvider.ts so additional providers slot in later).
  AI_PROVIDER: z.string().default('openai_compatible'),
  AI_BASE_URL: z.string().url().default('https://api.openai.com/v1'),
  AI_API_KEY: z.string().default(''),
  AI_DEFAULT_MODEL: z.string().default('gpt-4o-mini'),
  AI_MAX_TOKENS: z.coerce.number().int().positive().default(2048),
  AI_REQUEST_TIMEOUT_MS: z.coerce.number().int().positive().default(120_000),

  // PayPal Subscriptions.
  PAYPAL_ENV: z.enum(['sandbox', 'live']).default('sandbox'),
  PAYPAL_CLIENT_ID: z.string().default(''),
  PAYPAL_CLIENT_SECRET: z.string().default(''),
  PAYPAL_PLAN_ID_PRO: z.string().default(''),
  PAYPAL_WEBHOOK_ID: z.string().default(''),
  PAYPAL_VERIFY_WEBHOOKS: z.coerce.boolean().default(false),

  // Mail.
  MAIL_DRIVER: z.enum(['console', 'smtp']).default('console'),
  MAIL_FROM: z.string().default('Animica Chat <chat@animica.org>'),
  SMTP_HOST: z.string().optional().default(''),
  SMTP_PORT: z.coerce.number().int().positive().default(587),
  SMTP_USER: z.string().optional().default(''),
  SMTP_PASS: z.string().optional().default(''),

  // Integrations (coding agent).
  GITHUB_OAUTH_CLIENT_ID: z.string().default(''),
  GITHUB_OAUTH_CLIENT_SECRET: z.string().default(''),
  GITHUB_OAUTH_SCOPES: z.string().default('repo,read:user,user:email'),
  GITLAB_HOST: z.string().url().default('https://gitlab.com'),
  GITLAB_OAUTH_CLIENT_ID: z.string().default(''),
  GITLAB_OAUTH_CLIENT_SECRET: z.string().default(''),
  GITLAB_OAUTH_SCOPES: z.string().default('api,read_user,read_repository,write_repository'),
  AGENT_DEFAULT_WRITE_POLICY: z.enum(['ask', 'allow', 'deny']).default('ask'),

  // Admin bootstrap.
  ADMIN_EMAILS: z.string().default(''),

  // Usage limits.
  FREE_DAILY_MESSAGES: z.coerce.number().int().nonnegative().default(10),
  PRO_MONTHLY_MESSAGES: z.coerce.number().int().nonnegative().default(1000),
  MAX_MESSAGE_CHARS: z.coerce.number().int().positive().default(8000),
});

export type AppEnv = z.infer<typeof Env>;

function loadEnv(): AppEnv {
  const parsed = Env.safeParse(process.env);
  if (!parsed.success) {
    // eslint-disable-next-line no-console
    console.error('Invalid environment configuration:');
    for (const issue of parsed.error.issues) {
      // eslint-disable-next-line no-console
      console.error(`  - ${issue.path.join('.')}: ${issue.message}`);
    }
    process.exit(2);
  }
  return parsed.data;
}

export const env: AppEnv = loadEnv();

// Helpful derived values.
export const corsOrigins = env.CORS_ORIGINS.split(',')
  .map((s) => s.trim())
  .filter(Boolean);

export const adminEmails = new Set(
  env.ADMIN_EMAILS.split(',')
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean),
);

export const isProd = env.NODE_ENV === 'production';

export const paypalBaseUrl =
  env.PAYPAL_ENV === 'live'
    ? 'https://api-m.paypal.com'
    : 'https://api-m.sandbox.paypal.com';
