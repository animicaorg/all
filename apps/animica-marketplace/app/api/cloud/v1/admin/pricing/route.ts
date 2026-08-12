import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import {
  activePolicy,
  defaultPolicy,
  invalidatePolicyCache,
  quote,
  costOf,
  marginOf,
  type Policy,
  type Usage,
} from '@/lib/cloud/pricing';
import { adminActor, audit, readJson, optionalString } from '../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// /api/cloud/v1/admin/pricing — the pricing-policy control surface (§59, §76, §94).
//
//   GET   -> the active policy, the env bootstrap defaults, version history, recent changes.
//   POST  -> create a NEW PricingPolicy version and flip it active. Historical rows are NEVER
//            mutated (§88/§92: executions reference the policy version that priced them).
//            Every changed field gets a PricingChange audit row, the whole action gets a
//            CloudAuditLog row, and financially significant changes require confirm:true —
//            the first call returns the diff + warnings instead of applying (§76: selling
//            below cost warns loudly before it can be confirmed).

const BIGINT_FIELDS = [
  'baseCallNanm',
  'cpuMsNanm',
  'memMbMsNanm',
  'aiTokenInNanm',
  'aiTokenOutNanm',
  'egressKbNanm',
  'gpuMsNanm',
  'costCpuMsNanm',
  'costMemMbMsNanm',
  'costAiTokenNanm',
  'costEgressKbNanm',
  'costPerCallNanm',
  'freeTierMonthlyCeilingNanm',
  'anmUsdFloorMicros',
] as const;
const INT_FIELDS = [
  'platformFeeBps',
  'providerShareBps',
  'targetMarginBps',
  'freeExecutionsPerDay',
  'freeExecutionsPerMonth',
  'freeAiTokensPerDay',
] as const;
const BOOL_FIELDS = ['enforceMinMargin'] as const;

type BigField = (typeof BIGINT_FIELDS)[number];
type IntField = (typeof INT_FIELDS)[number];
type BoolField = (typeof BOOL_FIELDS)[number];
type PolicyFields = Record<BigField, bigint> & Record<IntField, number> & Record<BoolField, boolean>;

/** Customer prices whose reduction counts as financially significant. */
const CUSTOMER_PRICE_FIELDS: readonly BigField[] = [
  'baseCallNanm',
  'cpuMsNanm',
  'memMbMsNanm',
  'aiTokenInNanm',
  'aiTokenOutNanm',
  'egressKbNanm',
  'gpuMsNanm',
];

function fieldsOf(p: Policy): PolicyFields {
  const out = {} as PolicyFields;
  for (const f of BIGINT_FIELDS) out[f] = p[f];
  for (const f of INT_FIELDS) out[f] = p[f];
  for (const f of BOOL_FIELDS) out[f] = p[f];
  return out;
}

/** §76: warn when a proposed price sits below its estimated unit cost or the target margin. */
function belowCostWarnings(next: PolicyFields): string[] {
  const warnings: string[] = [];
  const pairs: Array<[BigField, BigField, string]> = [
    ['cpuMsNanm', 'costCpuMsNanm', 'CPU-ms'],
    ['memMbMsNanm', 'costMemMbMsNanm', 'MB-ms'],
    ['aiTokenInNanm', 'costAiTokenNanm', 'AI input token'],
    ['aiTokenOutNanm', 'costAiTokenNanm', 'AI output token'],
    ['egressKbNanm', 'costEgressKbNanm', 'egress KB'],
    ['baseCallNanm', 'costPerCallNanm', 'base call (vs fixed per-call infra cost)'],
  ];
  for (const [priceF, costF, label] of pairs) {
    if (next[priceF] < next[costF]) {
      warnings.push(
        `BELOW COST: ${label} price ${next[priceF]} nANM is below its estimated unit cost ${next[costF]} nANM — every unit sold at this rate loses money before the platform fee is even considered.`,
      );
    }
  }
  // Margin check on a representative execution: 1s CPU @ 256MB, 1000 AI tokens, 4KB egress.
  const policy: Policy = { ...defaultPolicy(), ...next, id: null, version: -1 };
  const usage: Usage = { cpuMs: 1000, memoryMbMs: 256_000n, aiTokensIn: 700, aiTokensOut: 300, egressBytes: 4096, gpuMs: 0 };
  const q = quote(usage, policy, {});
  const c = costOf(usage, policy);
  const fee = (q.totalNanm * BigInt(next.platformFeeBps)) / 10_000n;
  const m = marginOf(fee, c.totalNanm, next.targetMarginBps);
  if (m.negative) {
    warnings.push(
      `NEGATIVE MARGIN: on a representative execution (1s CPU / 256MB / 1000 AI tokens) the platform fee ${fee} nANM does not cover COGS ${c.totalNanm} nANM (contribution ${m.contributionNanm} nANM).`,
    );
  } else if (m.belowTarget && next.enforceMinMargin === false) {
    warnings.push(
      `BELOW TARGET MARGIN: representative-execution margin ${m.marginBps}bps is under the ${next.targetMarginBps}bps target and enforceMinMargin is OFF, so nothing will raise prices to the floor.`,
    );
  }
  return warnings;
}

export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const [active, history, changes] = await Promise.all([
      activePolicy(),
      prisma.pricingPolicy.findMany({ orderBy: { version: 'desc' }, take: 20 }),
      prisma.pricingChange.findMany({ orderBy: { createdAt: 'desc' }, take: 50 }),
    ]);
    return ok({ active, bootstrapDefaults: defaultPolicy(), history, changes });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    const actor = await adminActor(req);
    const body = await readJson(req);
    const reason = optionalString(body, 'reason');
    if (!reason) throw new ApiError(400, 'bad_request', "'reason' is required for a pricing change (audited)");
    const confirm = body.confirm === true;
    const changesIn = body.changes;
    if (!changesIn || typeof changesIn !== 'object') throw new ApiError(400, 'bad_request', "'changes' object is required");

    const current = await activePolicy();
    const cur = fieldsOf(current);
    const next: PolicyFields = { ...cur };

    // Parse + apply only known fields, with exact integer parsing (no float money).
    for (const f of BIGINT_FIELDS) {
      if (!(f in changesIn)) continue;
      const raw = changesIn[f];
      let v: bigint;
      try {
        v = BigInt(typeof raw === 'number' ? Math.trunc(raw) : String(raw));
      } catch {
        throw new ApiError(400, 'bad_request', `'${f}' must be an integer nANM value`);
      }
      if (v < 0n) throw new ApiError(400, 'bad_request', `'${f}' must be >= 0`);
      next[f] = v;
    }
    for (const f of INT_FIELDS) {
      if (!(f in changesIn)) continue;
      const v = Number(changesIn[f]);
      if (!Number.isInteger(v) || v < 0) throw new ApiError(400, 'bad_request', `'${f}' must be a non-negative integer`);
      next[f] = v;
    }
    for (const f of BOOL_FIELDS) {
      if (!(f in changesIn)) continue;
      if (typeof changesIn[f] !== 'boolean') throw new ApiError(400, 'bad_request', `'${f}' must be a boolean`);
      next[f] = changesIn[f];
    }

    // Structural validation (§89).
    if (next.platformFeeBps > 10_000 || next.providerShareBps > 10_000 || next.targetMarginBps > 10_000) {
      throw new ApiError(400, 'bad_request', 'bps fields must be <= 10000');
    }
    if (next.platformFeeBps + next.providerShareBps > 10_000) {
      throw new ApiError(400, 'bad_split', `platformFeeBps ${next.platformFeeBps} + providerShareBps ${next.providerShareBps} exceeds 100%`);
    }

    const diff: Array<{ field: string; oldValue: string; newValue: string }> = [];
    for (const f of [...BIGINT_FIELDS, ...INT_FIELDS, ...BOOL_FIELDS]) {
      if (String(cur[f]) !== String(next[f])) diff.push({ field: f, oldValue: String(cur[f]), newValue: String(next[f]) });
    }
    if (diff.length === 0) throw new ApiError(400, 'no_change', 'no field differs from the active policy');

    const warnings = belowCostWarnings(next);

    // Financially significant: split/margin machinery changes, any customer price cut, or any
    // below-cost warning. These require an explicit confirm after seeing the diff + warnings.
    const changed = new Set(diff.map((d) => d.field));
    const significant =
      changed.has('platformFeeBps') ||
      changed.has('providerShareBps') ||
      changed.has('targetMarginBps') ||
      changed.has('enforceMinMargin') ||
      CUSTOMER_PRICE_FIELDS.some((f) => changed.has(f) && next[f] < cur[f]) ||
      warnings.length > 0;

    if (significant && !confirm) {
      return ok(
        { applied: false, requiresConfirm: true, significant, warnings, diff, note: 'Re-submit with confirm:true to apply.' },
        { status: 409 },
      );
    }

    const note = optionalString(body, 'note', 500);
    const created = await prisma.$transaction(async (tx) => {
      const maxVersion = await tx.pricingPolicy.aggregate({ _max: { version: true } });
      const version = (maxVersion._max.version ?? 0) + 1;
      await tx.pricingPolicy.updateMany({ where: { active: true }, data: { active: false } });
      const row = await tx.pricingPolicy.create({
        data: { version, active: true, note: note || reason.slice(0, 200), createdBy: actor, ...next },
      });
      for (const d of diff) {
        await tx.pricingChange.create({
          data: { actor, field: d.field, oldValue: d.oldValue, newValue: d.newValue, policyId: row.id, reason },
        });
      }
      await audit(
        tx,
        actor,
        'pricing.policy_create',
        `pricing_policy:v${version}`,
        { activeVersion: current.version, fields: Object.fromEntries(diff.map((d) => [d.field, d.oldValue])) },
        { activeVersion: version, fields: Object.fromEntries(diff.map((d) => [d.field, d.newValue])), warnings },
        reason,
      );
      return row;
    });
    invalidatePolicyCache();

    return ok({ applied: true, policy: created, warnings, diff });
  } catch (e) {
    return err(e);
  }
}
