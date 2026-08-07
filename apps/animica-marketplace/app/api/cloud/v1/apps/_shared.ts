// Animica Python Cloud marketplace routes — shared request parsing + serialization.
//
// This file is intentionally tiny: real policy lives in lib/cloud/* (config, entitlements,
// settle, pricing). These are only the input-validation and shaping helpers the marketplace,
// agents, grants, reviews, reports, developers, search, stats and founding routes share.

import { prisma } from '@/lib/db';
import { ApiError } from '@/lib/api';
import { CloudCategory } from '@prisma/client';
import { isCapability } from '@/lib/cloud/config';

// Public app/agent slugs: 3-40 chars, [a-z0-9-], no leading/trailing hyphen.
export const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])?$/;
// Native Animica address (bech32m, ml_dsa_65). Same shape the executor's pay broker accepts.
export const ANM_ADDR_RE = /^anim1[0-9a-z]{20,90}$/;

export function str(v: unknown, max: number): string {
  return typeof v === 'string' ? v.trim().slice(0, max) : '';
}

export function requireStr(v: unknown, field: string, max: number, min = 1): string {
  const s = str(v, max);
  if (s.length < min) throw new ApiError(400, 'invalid', `${field} is required (${min}-${max} chars)`);
  return s;
}

export function parseSlug(v: unknown, field = 'slug'): string {
  const s = str(v, 40).toLowerCase();
  if (s.length < 3 || !SLUG_RE.test(s)) {
    throw new ApiError(400, 'invalid_slug', `${field} must be 3-40 chars of [a-z0-9-], no leading/trailing hyphen`);
  }
  return s;
}

/** Integer nANM from JSON (string | number | bigint). Non-negative, bounded to 1e18 nANM (1B ANM). */
export function parseNanm(v: unknown, field: string): bigint {
  let n: bigint;
  try {
    if (typeof v === 'bigint') n = v;
    else if (typeof v === 'number' && Number.isSafeInteger(v)) n = BigInt(v);
    else if (typeof v === 'string' && /^\d{1,19}$/.test(v.trim())) n = BigInt(v.trim());
    else throw new Error('shape');
  } catch {
    throw new ApiError(400, 'invalid_amount', `${field} must be an integer nANM amount (string or number)`);
  }
  if (n < 0n || n > 10n ** 18n) throw new ApiError(400, 'invalid_amount', `${field} out of range`);
  return n;
}

export function parseCategory(v: unknown): CloudCategory {
  const s = str(v, 32).toUpperCase();
  if (!(Object.values(CloudCategory) as string[]).includes(s)) {
    throw new ApiError(400, 'invalid_category', `category must be one of ${Object.values(CloudCategory).join(' | ')}`);
  }
  return s as CloudCategory;
}

/** Validate a capabilities array against the platform capability vocabulary. */
export function parseCaps(v: unknown, field = 'capabilities'): string[] {
  if (v == null) return [];
  if (!Array.isArray(v)) throw new ApiError(400, 'invalid', `${field} must be an array`);
  const out: string[] = [];
  for (const c of v) {
    const s = str(c, 32).toUpperCase();
    if (!isCapability(s)) throw new ApiError(400, 'invalid_capability', `unknown capability: ${String(c)}`);
    if (!out.includes(s)) out.push(s);
  }
  return out;
}

export function parseTags(v: unknown): string[] {
  if (v == null) return [];
  if (!Array.isArray(v)) throw new ApiError(400, 'invalid', 'tags must be an array of strings');
  const out: string[] = [];
  for (const t of v.slice(0, 10)) {
    const s = str(t, 32).toLowerCase();
    if (s.length >= 2 && !out.includes(s)) out.push(s);
  }
  return out;
}

export function parseExpiry(v: unknown, field = 'expiresAt'): Date | null {
  if (v == null || v === '') return null;
  const d = new Date(String(v));
  if (Number.isNaN(d.getTime())) throw new ApiError(400, 'invalid', `${field} must be an ISO-8601 date`);
  if (d.getTime() <= Date.now()) throw new ApiError(400, 'invalid', `${field} must be in the future`);
  if (d.getTime() > Date.now() + 366 * 24 * 3600 * 1000) {
    throw new ApiError(400, 'invalid', `${field} may be at most one year out`);
  }
  return d;
}

export function ratingOf(sum: number, count: number): { avg: number | null; count: number } {
  return { avg: count > 0 ? Math.round((sum / count) * 10) / 10 : null, count };
}

export function dayKeyUtc(now = new Date()): string {
  return now.toISOString().slice(0, 10);
}

/** The catalog card — everything a marketplace list needs, nothing private. */
export function appCard(app: {
  slug: string;
  name: string;
  tagline: string;
  category: string;
  iconEmoji: string;
  iconUrl: string | null;
  tags: string[];
  pricingModel: string;
  priceNanm: bigint;
  capabilities: string[];
  execCount: number;
  installCount: number;
  ratingSum: number;
  ratingCount: number;
  publishedAt: Date | null;
  owner: { handle: string | null; displayName: string | null };
  _count?: { functions: number };
}) {
  return {
    slug: app.slug,
    name: app.name,
    tagline: app.tagline,
    category: app.category,
    iconEmoji: app.iconEmoji,
    iconUrl: app.iconUrl,
    tags: app.tags,
    pricingModel: app.pricingModel,
    priceNanm: app.priceNanm,
    capabilities: app.capabilities,
    execCount: app.execCount,
    installCount: app.installCount,
    rating: ratingOf(app.ratingSum, app.ratingCount),
    publishedAt: app.publishedAt,
    functionCount: app._count?.functions ?? undefined,
    owner: { handle: app.owner.handle, displayName: app.owner.displayName },
  };
}

/** Resolve a live (published, unsuspended) app by slug, with its owner. */
export async function findPublishedApp(slug: string) {
  return prisma.cloudApp.findFirst({
    where: { slug: slug.toLowerCase(), status: 'PUBLISHED', suspendedAt: null },
    include: { owner: { select: { id: true, handle: true, address: true, displayName: true } } },
  });
}

/**
 * Fail-closed validation for a SPEND_ANM authorization: finite per-call and daily caps plus an
 * expiry are mandatory, and every listed payee must be a native anim1 address (§19, §20).
 */
export function validateSpendGrant(input: {
  maxPerCallNanm: bigint;
  maxPerExecNanm: bigint;
  dailyCapNanm: bigint;
  allowedPayees: string[];
  expiresAt: Date | null;
}) {
  if (input.maxPerCallNanm <= 0n) {
    throw new ApiError(400, 'caps_required', 'a SPEND_ANM authorization needs a per-call cap (maxPerCallNanm > 0)');
  }
  if (input.dailyCapNanm <= 0n) {
    throw new ApiError(400, 'caps_required', 'a SPEND_ANM authorization needs a daily cap (dailyCapNanm > 0)');
  }
  if (input.maxPerCallNanm > input.dailyCapNanm) {
    throw new ApiError(400, 'invalid', 'maxPerCallNanm cannot exceed dailyCapNanm');
  }
  if (input.maxPerExecNanm > 0n && input.maxPerExecNanm > input.dailyCapNanm) {
    throw new ApiError(400, 'invalid', 'maxPerExecNanm cannot exceed dailyCapNanm');
  }
  if (!input.expiresAt) {
    throw new ApiError(400, 'expiry_required', 'a SPEND_ANM authorization must carry an expiry (at most one year)');
  }
  if (input.allowedPayees.length > 20) throw new ApiError(400, 'invalid', 'at most 20 allowed payees');
  for (const p of input.allowedPayees) {
    if (!ANM_ADDR_RE.test(p)) throw new ApiError(400, 'invalid_payee', `not a native anim1 address: ${p.slice(0, 24)}`);
  }
}

export function parsePayees(v: unknown): string[] {
  if (v == null) return [];
  if (!Array.isArray(v)) throw new ApiError(400, 'invalid', 'allowedPayees must be an array of anim1 addresses');
  return v.map((p) => str(p, 100)).filter(Boolean);
}

export function pageParams(sp: URLSearchParams, defTake = 24, maxTake = 50) {
  const take = Math.min(maxTake, Math.max(1, Number(sp.get('limit')) || defTake));
  const skip = Math.max(0, Math.trunc(Number(sp.get('offset')) || 0));
  return { take, skip };
}
