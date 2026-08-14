import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { requireAdmin } from '@/lib/adminAuth';
import { ApiError } from '@/lib/api';
import { jsonSafe } from '@/lib/nanm';

// Shared plumbing for the Python Cloud admin APIs. Not a route (no route.ts in this dir).
//
// EVERY admin action that touches money, pricing or availability writes a CloudAuditLog row
// (actor, action, subject, before, after, reason) — audit() is the single funnel for that,
// and action handlers call it INSIDE the same transaction as the change wherever possible.

/** Admin gate + a stable actor string for audit rows. */
export async function adminActor(req: NextRequest): Promise<string> {
  const who = await requireAdmin(req);
  return who.via === 'account' && who.accountId ? who.accountId : 'token:MKT_ADMIN_TOKEN';
}

type Db = typeof prisma | Parameters<Parameters<typeof prisma.$transaction>[0]>[0];

export async function audit(
  db: Db,
  actor: string,
  action: string,
  subject: string,
  before: unknown,
  after: unknown,
  reason: string,
): Promise<void> {
  await (db as typeof prisma).cloudAuditLog.create({
    data: {
      actor,
      action,
      subject,
      before: JSON.stringify(jsonSafe(before ?? {})).slice(0, 20_000),
      after: JSON.stringify(jsonSafe(after ?? {})).slice(0, 20_000),
      reason: String(reason ?? '').slice(0, 2_000),
    },
  });
}

export async function readJson(req: NextRequest): Promise<Record<string, any>> {
  try {
    const body = await req.json();
    if (body && typeof body === 'object' && !Array.isArray(body)) return body;
  } catch {
    /* fall through */
  }
  throw new ApiError(400, 'bad_request', 'a JSON object body is required');
}

export function requireString(body: Record<string, any>, field: string, maxLen = 500): string {
  const v = body[field];
  if (typeof v !== 'string' || !v.trim()) throw new ApiError(400, 'bad_request', `'${field}' is required`);
  return v.trim().slice(0, maxLen);
}

export function optionalString(body: Record<string, any>, field: string, maxLen = 2000): string {
  const v = body[field];
  return typeof v === 'string' ? v.trim().slice(0, maxLen) : '';
}

export function pageParams(req: NextRequest, defaultTake = 50, maxTake = 200): { take: number; skip: number } {
  const url = new URL(req.url);
  const take = Math.min(maxTake, Math.max(1, Number(url.searchParams.get('take')) || defaultTake));
  const skip = Math.max(0, Number(url.searchParams.get('skip')) || 0);
  return { take, skip };
}
