import { NextRequest } from 'next/server';
import { createHash } from 'node:crypto';
import { prisma } from '@/lib/db';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { seal } from '@/lib/vault';
import { limits } from '@/lib/cloud/config';
import { resolvePlan, requireSlot } from '@/lib/cloud/entitlements';

export const dynamic = 'force-dynamic';

// Developer secrets, injected into the sandbox as environment values (§28).
//
//   GET    /api/cloud/v1/secrets                 -> names + hints ONLY. Values are write-only.
//   POST   /api/cloud/v1/secrets                 -> { name, value, functionId? } create/rotate
//   DELETE /api/cloud/v1/secrets?id= | ?name=[&functionId=]
//
// Values are sealed with AES-256-GCM (lib/vault.ts) before they touch the database and are
// NEVER returned by any endpoint after creation — the only consumer is the executor, which
// unseals them directly into the sandbox environment and redacts them from logs.
//
// Rows whose name starts with "__state__" are the functions' PERSIST_STATE storage, not
// developer secrets: they are invisible here and cannot be created or deleted through this API.

const NAME_RE = /^[A-Z][A-Z0-9_]{0,63}$/;

function hintFor(value: string): string {
  return value.length >= 8 ? `…${value.slice(-4)}` : '';
}

export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');

    const sp = req.nextUrl.searchParams;
    const where: any = { ownerId: ctx.accountId, NOT: { name: { startsWith: '__state__' } } };
    if (sp.get('functionId')) where.functionId = sp.get('functionId');

    const rows = await prisma.cloudSecret.findMany({
      where,
      orderBy: [{ createdAt: 'desc' }],
      take: 500,
      select: {
        id: true,
        name: true,
        hint: true,
        functionId: true,
        function: { select: { slug: true, name: true } },
        lastUsedAt: true,
        createdAt: true,
        updatedAt: true,
      },
    });

    return ok({
      secrets: rows.map((s) => ({
        id: s.id,
        name: s.name,
        hint: s.hint,
        functionId: s.functionId, // null => available to every function of this account
        functionSlug: s.function?.slug ?? null,
        lastUsedAt: s.lastUsedAt,
        createdAt: s.createdAt,
        updatedAt: s.updatedAt,
      })),
    });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    const body = await req.json().catch(() => {
      throw new ApiError(400, 'bad_json', 'request body must be valid JSON');
    });

    const name = String(body.name ?? '').trim();
    if (!NAME_RE.test(name)) {
      throw new ApiError(400, 'bad_name', 'secret names are ENV_STYLE: start with A-Z, then A-Z, 0-9 or _ (max 64 chars)');
    }
    const value = String(body.value ?? '');
    if (!value) throw new ApiError(400, 'bad_request', 'value is required');
    if (Buffer.byteLength(value, 'utf8') > limits.maxSecretBytes) {
      throw new ApiError(413, 'too_large', `secret values are limited to ${limits.maxSecretBytes} bytes`);
    }

    let functionId: string | null = null;
    if (body.functionId != null) {
      const fn = await prisma.cloudFunction.findUnique({
        where: { id: String(body.functionId) },
        select: { id: true, ownerId: true },
      });
      if (!fn || fn.ownerId !== ctx.accountId) throw new ApiError(404, 'not_found', 'function not found');
      functionId = fn.id;
    }

    // Idempotency keys on a SUBSTITUTE body: the secret value never reaches the idempotency
    // store, but its sha256 does — so a retry with the same value replays, while reusing the
    // key with a DIFFERENT value is correctly refused as a mismatch.
    const valueSha256 = createHash('sha256').update(value, 'utf8').digest('hex');
    return await withIdempotency(req, ctx, { name, functionId, valueSha256 }, async () => {
      // Rotation of an existing name is an update and consumes no new slot. Both branches are
      // scoped to (ownerId, functionId, name) — Postgres treats NULL functionId as distinct in
      // the unique index, so the explicit findFirst is what makes global-name rotation work.
      const existing = await prisma.cloudSecret.findFirst({
        where: { ownerId: ctx.accountId, functionId, name },
        select: { id: true },
      });

      const ciphertext = seal(value);
      const hint = hintFor(value);

      if (existing) {
        const updated = await prisma.cloudSecret.update({
          where: { id: existing.id },
          data: { ciphertext, hint },
          select: { id: true, name: true, hint: true, functionId: true, createdAt: true, updatedAt: true },
        });
        return { status: 200, data: { secret: updated, rotated: true, note: 'value stored; it will never be returned again' } };
      }

      const plan = await resolvePlan(ctx.accountId);
      const count = await prisma.cloudSecret.count({
        where: { ownerId: ctx.accountId, NOT: { name: { startsWith: '__state__' } } },
      });
      await requireSlot(ctx.accountId, 'max_secrets', count, plan);

      const created = await prisma.cloudSecret.create({
        data: { ownerId: ctx.accountId, functionId, name, ciphertext, hint },
        select: { id: true, name: true, hint: true, functionId: true, createdAt: true, updatedAt: true },
      });
      return { status: 201, data: { secret: created, rotated: false, note: 'value stored; it will never be returned again' } };
    });
  } catch (e) {
    return err(e);
  }
}

export async function DELETE(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');

    const sp = req.nextUrl.searchParams;
    const id = sp.get('id');
    const name = sp.get('name');
    if (!id && !name) throw new ApiError(400, 'bad_request', 'pass ?id= or ?name= (with optional &functionId=)');

    const where: any = { ownerId: ctx.accountId, NOT: { name: { startsWith: '__state__' } } };
    if (id) where.id = id;
    if (name) where.name = name;
    if (sp.get('functionId')) where.functionId = sp.get('functionId');

    const res = await prisma.cloudSecret.deleteMany({ where });
    if (res.count === 0) throw new ApiError(404, 'not_found', 'no matching secret');
    return ok({ deleted: res.count });
  } catch (e) {
    return err(e);
  }
}
