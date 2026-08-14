import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { flags, limits } from '@/lib/cloud/config';
import { resolvePlan, requireSlot, scheduleFloorMinutes, type ResolvedPlan } from '@/lib/cloud/entitlements';
import { parseCron, nextCronRun, minGapMinutes } from './cron';

export const dynamic = 'force-dynamic';

// Scheduled invocations (§29). CRUD on one collection route:
//
//   GET    /api/cloud/v1/schedules[?functionId=]
//   POST   /api/cloud/v1/schedules   { functionId, kind: 'interval'|'cron',
//                                      intervalMinutes? | cron?, payload?, enabled? }
//   PATCH  /api/cloud/v1/schedules   { id, intervalMinutes?|cron?, payload?, enabled? }
//   DELETE /api/cloud/v1/schedules?id=
//
// Enforcement: the plan's max_schedules slot count (live COUNT) and its minimum-interval
// floor (scheduleFloorMinutes). For cron expressions the floor is checked against the
// expression's ACTUAL minimum firing gap, not a guess. nextRunAt is persisted truthfully so
// the scheduler worker can pick the row up with a plain indexed query.

const MAX_PAYLOAD_BYTES = 32 * 1024;

interface Timing {
  kind: 'interval' | 'cron';
  intervalMinutes: number | null;
  cron: string | null;
  nextRunAt: Date;
}

function resolveTiming(body: any, plan: ResolvedPlan, now = new Date()): Timing {
  const floor = scheduleFloorMinutes(plan);
  const kind = String(body.kind ?? (body.cron != null ? 'cron' : 'interval'));
  if (kind === 'interval') {
    const iv = Number(body.intervalMinutes);
    if (!Number.isInteger(iv) || iv < 1) {
      throw new ApiError(400, 'bad_request', 'intervalMinutes must be a positive integer');
    }
    if (iv < floor) {
      const e = new ApiError(
        422,
        'below_schedule_floor',
        `the ${plan.key} plan allows schedules every ${floor} minutes or more (requested ${iv})`,
      );
      e.details = { floorMinutes: floor, requested: iv, upgradeUrl: '/cloud/pricing' };
      throw e;
    }
    return { kind, intervalMinutes: iv, cron: null, nextRunAt: new Date(now.getTime() + iv * 60_000) };
  }
  if (kind === 'cron') {
    let spec;
    try {
      spec = parseCron(String(body.cron ?? ''));
    } catch (e: any) {
      throw new ApiError(400, 'bad_cron', String(e?.message ?? 'invalid cron expression'));
    }
    const next = nextCronRun(spec, now);
    if (!next) throw new ApiError(422, 'never_fires', 'this cron expression never fires within the next year');
    const gap = minGapMinutes(spec, now);
    if (gap != null && gap < floor) {
      const e = new ApiError(
        422,
        'below_schedule_floor',
        `this cron fires every ${gap} minutes; the ${plan.key} plan floor is ${floor} minutes`,
      );
      e.details = { floorMinutes: floor, actualGapMinutes: gap, upgradeUrl: '/cloud/pricing' };
      throw e;
    }
    return { kind, intervalMinutes: null, cron: String(body.cron).trim().replace(/\s+/g, ' '), nextRunAt: next };
  }
  throw new ApiError(400, 'bad_request', 'kind must be "interval" or "cron"');
}

function encodePayload(v: unknown): string {
  const json = JSON.stringify(v ?? {});
  if (json === undefined) throw new ApiError(400, 'bad_request', 'payload must be JSON-serializable');
  if (Buffer.byteLength(json, 'utf8') > MAX_PAYLOAD_BYTES) {
    throw new ApiError(413, 'too_large', `payload is limited to ${MAX_PAYLOAD_BYTES} bytes`);
  }
  return json;
}

function serializeSchedule(s: {
  id: string;
  functionId: string;
  kind: string;
  intervalMinutes: number | null;
  cron: string | null;
  payloadJson: string;
  enabled: boolean;
  nextRunAt: Date | null;
  lastRunAt: Date | null;
  lastStatus: string | null;
  runsTotal: number;
  failures: number;
  disabledReason: string | null;
  createdAt: Date;
  updatedAt: Date;
  function?: { slug: string; name: string } | null;
}) {
  let payload: unknown = {};
  try {
    payload = JSON.parse(s.payloadJson || '{}');
  } catch {
    payload = {};
  }
  return {
    id: s.id,
    functionId: s.functionId,
    functionSlug: s.function?.slug ?? null,
    functionName: s.function?.name ?? null,
    kind: s.kind,
    intervalMinutes: s.intervalMinutes,
    cron: s.cron,
    payload,
    enabled: s.enabled,
    nextRunAt: s.nextRunAt,
    lastRunAt: s.lastRunAt,
    lastStatus: s.lastStatus,
    runsTotal: s.runsTotal,
    failures: s.failures,
    disabledReason: s.disabledReason,
    createdAt: s.createdAt,
    updatedAt: s.updatedAt,
  };
}

export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');

    const sp = req.nextUrl.searchParams;
    const where: any = { ownerId: ctx.accountId };
    if (sp.get('functionId')) where.functionId = sp.get('functionId');
    if (sp.get('enabled') === '1') where.enabled = true;
    if (sp.get('enabled') === '0') where.enabled = false;

    const rows = await prisma.cloudSchedule.findMany({
      where,
      include: { function: { select: { slug: true, name: true } } },
      orderBy: { createdAt: 'desc' },
      take: 500,
    });
    return ok({ schedules: rows.map(serializeSchedule), schedulingEnabled: flags.schedules });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    if (!flags.schedules) throw new ApiError(503, 'disabled', 'scheduled invocations are temporarily disabled');
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    const body = await req.json().catch(() => {
      throw new ApiError(400, 'bad_json', 'request body must be valid JSON');
    });

    const fn = await prisma.cloudFunction.findUnique({
      where: { id: String(body.functionId ?? '') },
      select: { id: true, ownerId: true, status: true, suspendedAt: true, suspendedReason: true },
    });
    if (!fn || fn.ownerId !== ctx.accountId) throw new ApiError(404, 'not_found', 'function not found');
    if (fn.suspendedAt) throw new ApiError(403, 'suspended', fn.suspendedReason || 'this function has been suspended');
    if (fn.status === 'ARCHIVED') throw new ApiError(409, 'archived', 'archived functions cannot be scheduled');

    return await withIdempotency(req, ctx, body, async () => {
      const plan = await resolvePlan(ctx.accountId);
      const count = await prisma.cloudSchedule.count({ where: { ownerId: ctx.accountId } });
      await requireSlot(ctx.accountId, 'max_schedules', count, plan);

      const timing = resolveTiming(body, plan);
      const payloadJson = encodePayload(body.payload);
      const enabled = body.enabled == null ? true : Boolean(body.enabled);

      const row = await prisma.cloudSchedule.create({
        data: {
          functionId: fn.id,
          ownerId: ctx.accountId,
          kind: timing.kind,
          intervalMinutes: timing.intervalMinutes,
          cron: timing.cron,
          payloadJson,
          enabled,
          nextRunAt: enabled ? timing.nextRunAt : null,
        },
        include: { function: { select: { slug: true, name: true } } },
      });
      return { status: 201, data: { schedule: serializeSchedule(row) } };
    });
  } catch (e) {
    return err(e);
  }
}

export async function PATCH(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    const body = await req.json().catch(() => {
      throw new ApiError(400, 'bad_json', 'request body must be valid JSON');
    });
    const id = String(body.id ?? '');
    if (!id) throw new ApiError(400, 'bad_request', 'id is required');

    const existing = await prisma.cloudSchedule.findUnique({ where: { id } });
    if (!existing || existing.ownerId !== ctx.accountId) throw new ApiError(404, 'not_found', 'schedule not found');

    const plan = await resolvePlan(ctx.accountId);
    const data: Record<string, unknown> = {};

    const timingChanged = body.kind != null || body.intervalMinutes != null || body.cron != null;
    const enabling = body.enabled != null ? Boolean(body.enabled) : existing.enabled;
    if (enabling && !flags.schedules) {
      throw new ApiError(503, 'disabled', 'scheduled invocations are temporarily disabled');
    }

    let timing: Timing | null = null;
    if (timingChanged) {
      // Merge existing timing with the patch so a partial update revalidates the whole thing.
      timing = resolveTiming(
        {
          kind: body.kind ?? existing.kind,
          intervalMinutes: body.intervalMinutes ?? existing.intervalMinutes,
          cron: body.cron ?? existing.cron,
        },
        plan,
      );
      data.kind = timing.kind;
      data.intervalMinutes = timing.intervalMinutes;
      data.cron = timing.cron;
    } else if (enabling && !existing.enabled) {
      // Re-enabling: the stored timing must still clear the CURRENT plan's floor.
      timing = resolveTiming(
        { kind: existing.kind, intervalMinutes: existing.intervalMinutes, cron: existing.cron },
        plan,
      );
    }

    if (body.payload !== undefined) data.payloadJson = encodePayload(body.payload);
    if (body.enabled != null) {
      data.enabled = enabling;
      data.disabledReason = enabling ? null : 'disabled by its owner';
    }
    if (enabling && timing) data.nextRunAt = timing.nextRunAt;
    if (!enabling) data.nextRunAt = null;

    if (!Object.keys(data).length) throw new ApiError(400, 'bad_request', 'no updatable fields in the request');

    const row = await prisma.cloudSchedule.update({
      where: { id },
      data,
      include: { function: { select: { slug: true, name: true } } },
    });
    return ok({ schedule: serializeSchedule(row) });
  } catch (e) {
    return err(e);
  }
}

export async function DELETE(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    const id = req.nextUrl.searchParams.get('id');
    if (!id) throw new ApiError(400, 'bad_request', 'pass ?id=');

    const res = await prisma.cloudSchedule.deleteMany({ where: { id, ownerId: ctx.accountId } });
    if (res.count === 0) throw new ApiError(404, 'not_found', 'schedule not found');
    return ok({ deleted: res.count });
  } catch (e) {
    return err(e);
  }
}
