import type { Metadata } from 'next';
import { Code, K, Callout, Table, PageNav } from '../doc';
import { activePolicy } from '@/lib/cloud/pricing';
import { CLOUD_PLAN_CATALOG, PLAN_ENTITLEMENTS, founding, type CloudPlanKey } from '@/lib/cloud/config';
import { formatAnm } from '@/lib/nanm';

// Live pricing straight from the ACTIVE PricingPolicy row (the same policy the executor
// prices with) — this page can never drift from what is actually charged.
export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: 'Pricing & economics — Animica Python Cloud',
  description: 'The exact metering formula, live unit rates, the revenue split and the commercial plans.',
};

const FORMULA = `price = baseCall
      + cpuMs        × cpuMsNanm          # host-measured container wall time
      + memoryMbMs   × memMbMsNanm        # configured MB × billed ms
      + aiTokensIn   × aiTokenInNanm
      + aiTokensOut  × aiTokenOutNanm
      + ceil(egressBytes / 1024) × egressKbNanm
      + gpuMs        × gpuMsNanm
      + perCallNanm                        # the developer's surcharge

price = max(price, marginFloor)            # see below
split: price == platformFee + developer (+ provider), exactly, in integer nANM`;

const FLOOR = `marginFloor = ceil( cogs × 10000² / ((10000 − targetMarginBps) × feeBps) )
# at the defaults (60% target margin, 20% fee) that is cogs × 12.5`;

function fmtUnits(n: number): string {
  return n === -1 ? 'unlimited*' : n.toLocaleString('en-US');
}

export default async function Page() {
  const policy = await activePolicy();
  const planKeys = CLOUD_PLAN_CATALOG.map((p) => p.key) as CloudPlanKey[];

  return (
    <>
      <h1>Pricing &amp; economics</h1>
      <p className="cd-lead">
        Execution is pay-per-use in ANM, metered per resource actually consumed, priced by a versioned
        policy (currently <b>v{policy.version}</b>), and split exactly between the platform and the
        developer. All money is integer nANM (1 ANM = 10⁹ nANM) — never floats.
      </p>

      <h2>The metering formula</h2>
      <Code title="what a successful execution costs the caller">{FORMULA}</Code>

      <h2>Live unit rates (policy v{policy.version})</h2>
      <Table
        head={['unit', 'rate', 'in ANM']}
        rows={[
          ['base per invocation', `${policy.baseCallNanm} nANM`, formatAnm(policy.baseCallNanm)],
          ['CPU millisecond', `${policy.cpuMsNanm} nANM`, `${formatAnm(policy.cpuMsNanm * 1000n)} / CPU-second`],
          ['memory MB-millisecond', `${policy.memMbMsNanm} nANM`, `${formatAnm(policy.memMbMsNanm * 1000n * 256n)} / second at 256 MB`],
          ['AI token in', `${policy.aiTokenInNanm} nANM`, `${formatAnm(policy.aiTokenInNanm * 1000n)} / 1k tokens`],
          ['AI token out', `${policy.aiTokenOutNanm} nANM`, `${formatAnm(policy.aiTokenOutNanm * 1000n)} / 1k tokens`],
          ['egress KB', `${policy.egressKbNanm} nANM`, `${formatAnm(policy.egressKbNanm * 1024n)} / MB`],
          ['GPU millisecond', `${policy.gpuMsNanm} nANM`, `${formatAnm(policy.gpuMsNanm * 1000n)} / GPU-second`],
        ]}
      />
      <p>
        Billed CPU is the <b>host-measured container wall time</b> — the resource the platform reserves for
        you — never the guest&apos;s self-reported number. Egress rounds up to whole KB. Rates are operator
        configuration: a new policy version reprices <em>future</em> executions only; historical rows keep
        the rates and fee they settled at.
      </p>

      <h2>The margin floor</h2>
      <p>
        The platform refuses to sell compute below its configured gross margin
        (target {policy.targetMarginBps / 100}%). When the metered price would be below the floor, the
        price is raised to it:
      </p>
      <Code title="minimum price">{FLOOR}</Code>
      <p>
        In practice the floor dominates for long-running, low-rate executions (e.g. a function that waits
        60 s on an upstream), and the metered sum dominates for short bursts. The pre-execution{' '}
        <K>POST /api/cloud/v1/estimate</K> shows both the typical and worst case with the floor applied —
        the number shown is the number charged.
      </p>

      <h2>The split</h2>
      <ul>
        <li>
          <b>Platform fee:</b> {policy.platformFeeBps / 100}% ({policy.platformFeeBps} bps) of the price —
          Founding Developers pay {founding.feeBps / 100}% for their first {founding.feeMonths} months.
        </li>
        <li>
          <b>Compute provider:</b> {policy.providerShareBps / 100}% when a fleet provider ran the execution
          (0 when the platform ran it locally) — see <a href="/docs/cloud/providers">Compute providers</a>.
        </li>
        <li>
          <b>Developer:</b> the exact remainder. Platform and provider shares are computed by basis points;
          the developer takes what is left, so integer rounding can never mint or lose a nANM:{' '}
          <K>price == platformFee + developer + provider</K> is asserted before settlement is allowed to
          commit.
        </li>
        <li>
          The fee rate is <b>snapshotted</b> onto every execution and purchase — later policy changes never
          rewrite history.
        </li>
      </ul>

      <h2>Failures</h2>
      <p>
        You pay for resources your request actually consumed, never for an outcome that did not happen:
      </p>
      <ul>
        <li>
          <b>Rejected before running</b> (quota, funds, capacity): charged nothing; consumed quota is
          refunded.
        </li>
        <li>
          <b>Failed / timed out</b>: real CPU, memory and AI were burned, so the caller pays the metered
          resource cost — with <b>no developer surcharge and no margin floor</b>. The developer earns
          nothing from a failed run.
        </li>
      </ul>

      <h2>Free tier</h2>
      <ul>
        <li>
          {policy.freeExecutionsPerDay} executions/day and {policy.freeExecutionsPerMonth}/month per caller
          (anonymous callers are counted by salted IP hash), on public, auth-optional, zero-surcharge
          functions.
        </li>
        <li>{policy.freeAiTokensPerDay.toLocaleString('en-US')} free AI tokens/day.</li>
        <li>
          Calls to <b>your own</b> functions consume your plan&apos;s included quota instead of ANM.
        </li>
        <li>
          A platform-wide monthly free-tier cost ceiling can pause free execution rather than run it at an
          unbounded loss.
        </li>
      </ul>

      <h2>Plans (USD subscriptions)</h2>
      <p>
        Execution is paid in ANM; plans set your <b>limits and entitlements</b> — how many functions you can
        publish, monthly included executions, concurrency, schedule frequency, log retention, priority.
      </p>
      <Table
        head={['', ...CLOUD_PLAN_CATALOG.map((p) => `${p.name}${p.priceUsdCents ? ` $${(p.priceUsdCents / 100).toFixed(0)}/mo` : ''}${p.contactSales ? ' (from)' : ''}`)]}
        rows={[
          ['functions', ...planKeys.map((k) => fmtUnits(PLAN_ENTITLEMENTS[k].max_functions))],
          ['apps / agents', ...planKeys.map((k) => `${fmtUnits(PLAN_ENTITLEMENTS[k].max_apps)} / ${fmtUnits(PLAN_ENTITLEMENTS[k].max_agents)}`)],
          ['executions / month', ...planKeys.map((k) => fmtUnits(PLAN_ENTITLEMENTS[k].monthly_executions))],
          ['compute units (CPU-s) / month', ...planKeys.map((k) => fmtUnits(PLAN_ENTITLEMENTS[k].monthly_compute_units))],
          ['AI units (1k tokens) / month', ...planKeys.map((k) => fmtUnits(PLAN_ENTITLEMENTS[k].monthly_ai_units))],
          ['concurrency', ...planKeys.map((k) => String(PLAN_ENTITLEMENTS[k].max_concurrency))],
          ['schedules (min interval)', ...planKeys.map((k) => `${fmtUnits(PLAN_ENTITLEMENTS[k].max_schedules)} (${PLAN_ENTITLEMENTS[k].min_schedule_minutes}m)`)],
          ['log retention', ...planKeys.map((k) => `${PLAN_ENTITLEMENTS[k].log_retention_days}d`)],
          ['marketplace publishing', ...planKeys.map((k) => (PLAN_ENTITLEMENTS[k].marketplace_publishing ? 'yes' : 'no'))],
          ['metered overages', ...planKeys.map((k) => (PLAN_ENTITLEMENTS[k].overage_allowed ? 'yes' : 'no'))],
        ]}
      />
      <p>
        *&quot;unlimited&quot; means no numeric cap from the plan — the platform&apos;s hard safety ceilings still apply
        and every expensive resource still meters. Past an included allowance, plans with overages accrue
        metered USD charges; plans without are refused with a clear <K>402 quota_exceeded</K>.
      </p>

      <Callout>
        <b>What is never charged:</b> deploying costs the developer 0 nANM (the platform pays the anchor
        tx gas), and there are no storage fees for source, versions or logs within your plan&apos;s retention.
      </Callout>
      <PageNav current="/docs/cloud/pricing" />
    </>
  );
}
