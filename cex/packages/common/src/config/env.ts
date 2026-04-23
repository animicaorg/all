import { z } from "zod";
import fs from "node:fs";
import path from "node:path";

export const baseEnvSchema = z.object({
  SERVICE_NAME: z.string().min(1),
  PORT: z.coerce.number().default(3000),
  LOG_LEVEL: z.string().default("info"),
  NATS_URL: z.string().url(),
  REDIS_URL: z.string().url(),
  DB_HOST: z.string().min(1),
  DB_PORT: z.coerce.number().default(5432),
  DB_USER: z.string().min(1),
  DB_PASSWORD: z.string().min(1),
  DB_NAME: z.string().min(1)
});

export type BaseEnv = z.infer<typeof baseEnvSchema>;

const MONOREPO_ENV_FILE = path.join("ops", "env", ".env");
const DEFAULT_DEV_ADMIN_API_KEY = "dev-admin-key";
const LOCALHOST = "localhost";
const DISABLE_LOCALHOST_ALIAS_ENV = "CEX_DISABLE_LOCALHOST_ALIAS";

let hasLoadedWorkspaceEnv = false;

const findWorkspaceEnvFile = (): string | null => {
  let currentDir = process.cwd();

  while (true) {
    const candidate = path.join(currentDir, MONOREPO_ENV_FILE);
    if (fs.existsSync(candidate)) {
      return candidate;
    }

    const parentDir = path.dirname(currentDir);
    if (parentDir === currentDir) {
      return null;
    }

    currentDir = parentDir;
  }
};

const hydrateDerivedEnv = () => {
  if (!process.env.DATABASE_URL) {
    const host = process.env.DB_HOST;
    const port = process.env.DB_PORT ?? "5432";
    const user = process.env.DB_USER;
    const password = process.env.DB_PASSWORD;
    const dbName = process.env.DB_NAME;

    if (host && user && dbName) {
      const auth = password
        ? `${encodeURIComponent(user)}:${encodeURIComponent(password)}`
        : encodeURIComponent(user);
      process.env.DATABASE_URL = `postgresql://${auth}@${host}:${port}/${encodeURIComponent(dbName)}`;
    }
  }

  if (!process.env.ADMIN_API_KEY) {
    if (process.env.ADMIN_KEY) {
      process.env.ADMIN_API_KEY = process.env.ADMIN_KEY;
    } else if ((process.env.NODE_ENV ?? "development") !== "production") {
      process.env.ADMIN_API_KEY = DEFAULT_DEV_ADMIN_API_KEY;
    }
  }
};

const isRunningInsideContainer = (): boolean => {
  return fs.existsSync("/.dockerenv") || fs.existsSync("/run/.containerenv");
};

const rewriteUrlHostname = (value: string | undefined, from: string, to: string): string | undefined => {
  if (!value) {
    return value;
  }

  try {
    const parsed = new URL(value);
    if (parsed.hostname === from) {
      parsed.hostname = to;
      return parsed.toString();
    }
  } catch {
    return value;
  }

  return value;
};

const normalizeDockerHostAliasesForHostDev = () => {
  const disableAlias = process.env[DISABLE_LOCALHOST_ALIAS_ENV]?.toLowerCase();
  if (disableAlias === "1" || disableAlias === "true" || isRunningInsideContainer()) {
    return;
  }

  if (process.env.DB_HOST === "postgres") {
    process.env.DB_HOST = LOCALHOST;
  }

  process.env.DATABASE_URL = rewriteUrlHostname(process.env.DATABASE_URL, "postgres", LOCALHOST);
  process.env.REDIS_URL = rewriteUrlHostname(process.env.REDIS_URL, "redis", LOCALHOST);
  process.env.NATS_URL = rewriteUrlHostname(process.env.NATS_URL, "nats", LOCALHOST);
};

export const loadWorkspaceEnv = () => {
  if (hasLoadedWorkspaceEnv) {
    return;
  }

  hasLoadedWorkspaceEnv = true;

  const envFileFromOverride = process.env.CEX_ENV_FILE?.trim();
  const envFilePath = envFileFromOverride
    ? path.resolve(envFileFromOverride)
    : findWorkspaceEnvFile();

  if (envFilePath && fs.existsSync(envFilePath) && typeof process.loadEnvFile === "function") {
    process.loadEnvFile(envFilePath);
  }

  normalizeDockerHostAliasesForHostDev();
  hydrateDerivedEnv();
};

export const loadEnv = <T extends z.ZodTypeAny>(schema: T) => {
  loadWorkspaceEnv();

  const result = schema.safeParse(process.env);
  if (!result.success) {
    const formatted = result.error.flatten().fieldErrors;
    throw new Error(`Invalid environment configuration: ${JSON.stringify(formatted)}`);
  }
  return result.data as z.infer<T>;
};
