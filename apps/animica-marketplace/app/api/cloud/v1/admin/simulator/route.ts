import { NextRequest } from 'next/server';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { activePolicy, quote, costOf, splitOf, type Policy, type Usage } from '@/lib/cloud/pricing';
import { CLOUD_PLAN_CATALOG } from '@/lib/cloud/config';
import { readJson } from '../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// POST /api/cloud/v1/admin/simulator — the economic simulator (§95).
//
// Pure arithmetic over the SAME pricing engine that prices real executions (quote/costOf/
// splitOf from lib/cloud/pricing.ts), so the simulation and production can never disagree
// about the model. Nothing here reads or writes business records — every output is an
// ESTIMATE and is labelled as such in the response.
//
// Inputs (all optional; defaults shown):
//   monthlyUsers            1000
//   paidConversionBps       500        (5% of users become paying subscribers)
//   executionsPerUser       100        (per month)
//   avgCpuMs                500
//   avgMemoryMb             256
//   aiTokensPerExecution    0
//   avgEgressBytes          4096
//   fleetShareBps           0          (share of executions run by community providers)
//   anmUsdMicros            null       (USD price of 1 ANM in micro-dollars; omit => no USD)
//   platformFeeBps          policy     (override)
//   providerShareBps        policy     (override)
//   subscriptionMix         {developer: n, pro: n, business: n, enterprise: n} subscriber counts;
//                           when omitted, paid users are spread across developer/pro 80/20.

function intIn(body: Record<string, any>, field: string, dflt: number, min = 0, max = 1_000_000_000): number {
  const raw = body[field];
  if (raw == null) return dflt;
  const v = Number(raw);
  if (!Number.isFinite(v) || v < min || v > max) throw new ApiError(400, 'bad_request', `'${field}' must be ${min}..${max}`);
  return Math.trunc(v);
}

export async function POST(req: NextRequest) {
  try {
    await requireAdmin(req);
    const body = await readJson(req);
    const base = await activePolicy();

    const monthlyUsers = intIn(body, 'monthlyUsers', 1000);
    const paidConversionBps = intIn(body, 'paidConversionBps', 500, 0, 10_000);
    const executionsPerUser = intIn(body, 'executionsPerUser', 100);
    const avgCpuMs = intIn(body, 'avgCpuMs', 500, 1, 300_000);
    const avgMemoryMb = intIn(body, 'avgMemoryMb', 256, 64, 1024);
    const aiTokensPerExecution = intIn(body, 'aiTokensPerExecution', 0, 0, 100_000);
    const avgEgressBytes = intIn(body, 'avgEgressBytes', 4096, 0, 10_000_000);
    const fleetShareBps = intIn(body, 'fleetShareBps', 0, 0, 10_000);
    const platformFeeBps = intIn(body, 'platformFeeBps', base.platformFeeBps, 0, 10_000);
    const providerShareBps = intIn(body, 'providerShareBps', base.providerShareBps, 0, 10_000);
    if (platformFeeBps + providerShareBps > 10_000) {
      throw new ApiError(400, 'bad_split', 'platformFeeBps + providerShareBps exceeds 100%');
    }

    let anmUsdMicros: bigint | null = null;
    if (body.anmUsdMicros != null) {
      try {
        anmUsdMicros = BigInt(String(body.anmUsdMicros));
      } catch {
        throw new ApiError(400, 'bad_request', "'anmUsdMicros' must be an integer (micro-dollars per ANM)");
      }
      if (anmUsdMicros <= 0n) anmUsdMicros = null;
    }

    const policy: Policy = { ...base, platformFeeBps, providerShareBps };

    // Per-execution economics through the REAL pricing engine.
    const usage: Usage = {
      cpuMs: avgCpuMs,
      memoryMbMs: BigInt(avgCpuMs) * BigInt(avgMemoryMb),
      aiTokensIn: Math.round(aiTokensPerExecution * 0.7),
      aiTokensOut: Math.round(aiTokensPerExecution * 0.3),
      egressBytes: avgEgressBytes,
      gpuMs: 0,
    };
    const q = quote(usage, policy, {});
    const c = costOf(usage, policy);
    const localSplit = splitOf(q.totalNanm, platformFeeBps, providerShareBps, false);
    const fleetSplit = splitOf(q.totalNanm, platformFeeBps, providerShareBps, true);

    const executions = BigInt(monthlyUsers) * BigInt(executionsPerUser);
    const fleetExecutions = (executions * BigInt(fleetShareBps)) / 10_000n;
    const localExecutions = executions - fleetExecutions;

    const grossVolumeNanm = executions * q.totalNanm;
    const platformRevenueNanm = executions * localSplit.platformFeeNanm; // fee identical on both lanes
    const providerPayoutsNanm = fleetExecutions * fleetSplit.providerNanm;
    const developerPayoutsNanm = localExecutions * localSplit.developerNanm + fleetExecutions * fleetSplit.developerNanm;
    const cogsNanm = executions * c.totalNanm;
    const grossProfitNanm = platformRevenueNanm - cogsNanm;
    const marginBps = platformRevenueNanm > 0n ? Number((grossProfitNanm * 10_000n) / platformRevenueNanm) : null;

    // Subscription MRR from the mix (USD side, marketing-catalog prices).
    const paidUsers = Math.round((monthlyUsers * paidConversionBps) / 10_000);
    const catalog = Object.fromEntries(CLOUD_PLAN_CATALOG.map((p) => [p.key, p.priceUsdCents]));
    let mix: Record<string, number>;
    if (body.subscriptionMix && typeof body.subscriptionMix === 'object') {
      mix = {};
      for (const [k, v] of Object.entries(body.subscriptionMix as Record<string, unknown>)) {
        if (!(k in catalog)) throw new ApiError(400, 'bad_request', `unknown plan '${k}' in subscriptionMix`);
        const n = Number(v);
        if (!Number.isFinite(n) || n < 0) throw new ApiError(400, 'bad_request', `subscriptionMix.${k} must be >= 0`);
        mix[k] = Math.trunc(n);
      }
    } else {
      mix = { developer: Math.round(paidUsers * 0.8), pro: paidUsers - Math.round(paidUsers * 0.8) };
    }
    let mrrCents = 0;
    for (const [k, n] of Object.entries(mix)) mrrCents += (catalog[k] ?? 0) * n;

    const usd = (nanm: bigint) => (anmUsdMicros ? Number((nanm * anmUsdMicros) / 10_000_000_000_000n) : null);

    return ok({
      estimates: true,
      disclaimer: 'ESTIMATES from the live pricing engine and your inputs — not actuals. Actuals live on /admin/profitability.',
      inputs: {
        monthlyUsers,
        paidConversionBps,
        executionsPerUser,
        avgCpuMs,
        avgMemoryMb,
        aiTokensPerExecution,
        avgEgressBytes,
        fleetShareBps,
        platformFeeBps,
        providerShareBps,
        anmUsdMicros,
        subscriptionMix: mix,
        policyVersion: base.version,
      },
      perExecution: {
        priceNanm: q.totalNanm,
        raisedByMarginFloor: q.raisedByMargin,
        cogsNanm: c.totalNanm,
        platformFeeNanm: localSplit.platformFeeNanm,
        developerNanm_local: localSplit.developerNanm,
        developerNanm_fleet: fleetSplit.developerNanm,
        providerNanm_fleet: fleetSplit.providerNanm,
      },
      monthly: {
        executions,
        fleetExecutions,
        grossVolumeNanm, // customer spend — NOT revenue (§80)
        platformRevenueNanm,
        developerPayoutsNanm,
        providerPayoutsNanm,
        cogsNanm,
        grossProfitNanm,
        grossMarginBps: marginBps,
        anmRequiredNanm: grossVolumeNanm, // ANM users must hold/spend to sustain this volume
        usd: anmUsdMicros
          ? {
              grossVolumeCents: usd(grossVolumeNanm),
              platformRevenueCents: usd(platformRevenueNanm),
              cogsCents: usd(cogsNanm),
              grossProfitCents: usd(grossProfitNanm),
              providerPayoutsCents: usd(providerPayoutsNanm),
            }
          : null,
        subscriptionMrrCents: mrrCents,
        paidUsers,
      },
    });
  } catch (e) {
    return err(e);
  }
}
