/**
 * Typed environment loader for build-time URLs.
 *
 * NOTE: Astro only exposes PUBLIC_* variables to client bundles. We primarily
 * read unprefixed ANIMICA_* values at build time, then pass them into inline
 * scripts so no secrets are exposed.
 */

type EnvKey =
  | 'ANIMICA_RPC_URL'
  | 'ANIMICA_EXPLORER_URL'
  | 'ANIMICA_EXPLORER2_URL'
  | 'ANIMICA_DOCS_URL'
  | 'ANIMICA_GITHUB_URL'
  | 'ANIMICA_FAUCET_URL'
  | 'ANIMICA_POOL_URL'
  | 'ANIMICA_DISCORD_URL'
  | 'ANIMICA_TELEGRAM_URL'
  | 'ANIMICA_X_URL'
  | 'ANIMICA_STUDIO_URL'
  | 'ANIMICA_CHAIN_ID';

type RawEnv = Record<string, string | boolean | undefined>;

const rawEnv: RawEnv = {
  ...(typeof process !== 'undefined' ? process.env : {}),
  ...(((import.meta as any)?.env ?? {}) as RawEnv),
};

const DEFAULTS = {
  ANIMICA_RPC_URL: 'http://127.0.0.1:8545',
  ANIMICA_EXPLORER_URL: 'https://explorer.animica.org',
  ANIMICA_EXPLORER2_URL: '',
  ANIMICA_DOCS_URL: 'https://docs.animica.org',
  ANIMICA_GITHUB_URL: 'https://github.com/animicaorg/all',
  ANIMICA_FAUCET_URL: '',
  ANIMICA_POOL_URL: '',
  ANIMICA_DISCORD_URL: 'https://discord.gg/animica',
  ANIMICA_TELEGRAM_URL: '',
  ANIMICA_X_URL: 'https://x.com/animica',
  ANIMICA_STUDIO_URL: 'https://studio.animica.org',
  ANIMICA_CHAIN_ID: '1',
};

const LEGACY_ALIASES: Record<EnvKey, string[]> = {
  ANIMICA_RPC_URL: ['PUBLIC_ANIMICA_RPC_URL', 'PUBLIC_RPC_URL'],
  ANIMICA_EXPLORER_URL: ['PUBLIC_ANIMICA_EXPLORER_URL', 'PUBLIC_EXPLORER_URL'],
  ANIMICA_EXPLORER2_URL: ['PUBLIC_ANIMICA_EXPLORER2_URL'],
  ANIMICA_DOCS_URL: ['PUBLIC_ANIMICA_DOCS_URL', 'PUBLIC_DOCS_URL'],
  ANIMICA_GITHUB_URL: ['PUBLIC_ANIMICA_GITHUB_URL'],
  ANIMICA_FAUCET_URL: ['PUBLIC_ANIMICA_FAUCET_URL'],
  ANIMICA_POOL_URL: ['PUBLIC_ANIMICA_POOL_URL'],
  ANIMICA_DISCORD_URL: ['PUBLIC_ANIMICA_DISCORD_URL'],
  ANIMICA_TELEGRAM_URL: ['PUBLIC_ANIMICA_TELEGRAM_URL'],
  ANIMICA_X_URL: ['PUBLIC_ANIMICA_X_URL'],
  ANIMICA_STUDIO_URL: ['PUBLIC_ANIMICA_STUDIO_URL', 'PUBLIC_STUDIO_URL'],
  ANIMICA_CHAIN_ID: ['PUBLIC_ANIMICA_CHAIN_ID', 'PUBLIC_CHAIN_ID'],
};

function readString(key: EnvKey): string {
  const candidates = [key, ...(LEGACY_ALIASES[key] || [])];
  for (const k of candidates) {
    const val = rawEnv[k];
    if (typeof val === 'string' && val.trim() !== '') {
      return val.trim();
    }
  }
  return DEFAULTS[key] ?? '';
}

function readOptionalString(key: EnvKey): string | undefined {
  const value = readString(key).trim();
  return value ? value : undefined;
}

function readURL(key: EnvKey): string {
  const raw = readString(key);
  try {
    const u = new URL(raw);
    if (!u.protocol || !u.host) throw new Error('not absolute');
    return u.toString().replace(/\/+$/, '');
  } catch {
    throw new Error(`Invalid URL in ${key}: ${raw}`);
  }
}

function readOptionalURL(key: EnvKey): string | undefined {
  const raw = readOptionalString(key);
  if (!raw) return undefined;
  try {
    const u = new URL(raw);
    if (!u.protocol || !u.host) throw new Error('not absolute');
    return u.toString().replace(/\/+$/, '');
  } catch {
    throw new Error(`Invalid URL in ${key}: ${raw}`);
  }
}

function readPositiveInt(key: EnvKey): number {
  const raw = readString(key);
  const n = Number(raw);
  if (!Number.isInteger(n) || n <= 0) {
    throw new Error(`Invalid positive integer in ${key}: ${raw}`);
  }
  return n;
}

export interface PublicEnv {
  RPC_URL: string;
  EXPLORER_URL: string;
  EXPLORER2_URL?: string;
  DOCS_URL: string;
  GITHUB_URL: string;
  FAUCET_URL?: string;
  POOL_URL?: string;
  DISCORD_URL?: string;
  TELEGRAM_URL?: string;
  X_URL?: string;
  STUDIO_URL: string;
  CHAIN_ID: number;
}

export const ENV: Readonly<PublicEnv> = Object.freeze({
  RPC_URL: readURL('ANIMICA_RPC_URL'),
  EXPLORER_URL: readURL('ANIMICA_EXPLORER_URL'),
  EXPLORER2_URL: readOptionalURL('ANIMICA_EXPLORER2_URL'),
  DOCS_URL: readURL('ANIMICA_DOCS_URL'),
  GITHUB_URL: readURL('ANIMICA_GITHUB_URL'),
  FAUCET_URL: readOptionalURL('ANIMICA_FAUCET_URL'),
  POOL_URL: readOptionalURL('ANIMICA_POOL_URL'),
  DISCORD_URL: readOptionalURL('ANIMICA_DISCORD_URL'),
  TELEGRAM_URL: readOptionalURL('ANIMICA_TELEGRAM_URL'),
  X_URL: readOptionalURL('ANIMICA_X_URL'),
  STUDIO_URL: readURL('ANIMICA_STUDIO_URL'),
  CHAIN_ID: readPositiveInt('ANIMICA_CHAIN_ID'),
});

export const rpcUrl = ENV.RPC_URL;
export const explorerUrl = ENV.EXPLORER_URL;
export const explorer2Url = ENV.EXPLORER2_URL;
export const docsUrl = ENV.DOCS_URL;
export const githubUrl = ENV.GITHUB_URL;
export const faucetUrl = ENV.FAUCET_URL;
export const poolUrl = ENV.POOL_URL;
export const discordUrl = ENV.DISCORD_URL;
export const telegramUrl = ENV.TELEGRAM_URL;
export const xUrl = ENV.X_URL;
export const studioUrl = ENV.STUDIO_URL;
export const chainId = ENV.CHAIN_ID;
