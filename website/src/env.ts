/**
 * Typed environment loader for PUBLIC_* variables in Astro/Vite.
 * These values are exposed to client-side code at build time.
 *
 * Required (see .env.example):
 *  - PUBLIC_STUDIO_URL
 *  - PUBLIC_EXPLORER_URL
 *  - PUBLIC_DOCS_URL
 *  - PUBLIC_RPC_URL
 *  - PUBLIC_CHAIN_ID
 */

type PublicKeys =
  | 'PUBLIC_STUDIO_URL'
  | 'PUBLIC_EXPLORER_URL'
  | 'PUBLIC_DOCS_URL'
  | 'PUBLIC_RPC_URL'
  | 'PUBLIC_CHAIN_ID';

type RawEnv = Record<string, string | boolean | undefined> & {
  PUBLIC_STUDIO_URL?: string;
  PUBLIC_EXPLORER_URL?: string;
  PUBLIC_DOCS_URL?: string;
  PUBLIC_RPC_URL?: string;
  PUBLIC_CHAIN_ID?: string;
};

const rawEnv = (import.meta.env as unknown) as RawEnv;

/** Ensure a variable exists and is a non-empty string. */
function readString<K extends PublicKeys>(key: K): string {
  const v = rawEnv[key];
  if (typeof v === 'string' && v.trim() !== '') return v;
  throw new Error(`Missing required env var: ${key}`);
}

/** Ensure value parses as an absolute URL, return the normalized string. */
function readURL<K extends PublicKeys>(key: K): string {
  const s = readString(key);
  try {
    const u = new URL(s);
    if (!u.protocol || !u.host) throw new Error('not absolute');
    return u.toString().replace(/\/+$/, ''); // strip trailing slashes for consistency
  } catch (err) {
    throw new Error(`Invalid URL in ${key}: ${s}`);
  }
}

/** Parse positive integer (e.g., chain id). */
function readPositiveInt<K extends PublicKeys>(key: K): number {
  const s = readString(key);
  const n = Number(s);
  if (!Number.isInteger(n) || n <= 0) {
    throw new Error(`Invalid positive integer in ${key}: ${s}`);
  }
  return n;
}

/** Typed, validated, and frozen public environment. */
export interface PublicEnv {
  STUDIO_URL: string;
  EXPLORER_URL: string;
  DOCS_URL: string;
  RPC_URL: string;
  CHAIN_ID: number;
}

export const ENV: Readonly<PublicEnv> = Object.freeze({
  STUDIO_URL: readURL('PUBLIC_STUDIO_URL'),
  EXPLORER_URL: readURL('PUBLIC_EXPLORER_URL'),
  DOCS_URL: readURL('PUBLIC_DOCS_URL'),
  RPC_URL: readURL('PUBLIC_RPC_URL'),
  CHAIN_ID: readPositiveInt('PUBLIC_CHAIN_ID'),
});

/**
 * Convenience getters (tree-shaking friendly)
 * Usage: import { studioUrl } from '@/env'
 */
export const studioUrl = ENV.STUDIO_URL;
export const explorerUrl = ENV.EXPLORER_URL;
export const docsUrl = ENV.DOCS_URL;
export const rpcUrl = ENV.RPC_URL;
export const chainId = ENV.CHAIN_ID;
