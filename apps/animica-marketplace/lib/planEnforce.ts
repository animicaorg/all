// Plan-limit enforcement sweep (docs/subscriptions.md: "Downgrades/lapses never delete
// resources"). Called from the worker-runner tick, and safe to run standalone. Three moves:
//   1. PAST_DUE/GRACE_PERIOD subscriptions whose dunning window lapsed -> SUSPENDED
//      (getAccountPlan then resolves them to free limits, so step 2 shelves this same tick).
//   2. Per account: shelve over-limit resources, keeping the OLDEST within the limit —
//      predictable + documented; the user can swap actives via POST /billing/keep.
//   3. Auto-restore a kind ONLY when everything shelved of that kind fits the current
//      limit (conservative full-set restore after an upgrade/payment recovery); partial
//      restores stay user-driven so we never guess which resources the user wants live.

import { prisma } from './db';
import { getAccountPlan } from './plan';

// Same copy the billing/keep route stamps, so the /workers UI shows one consistent reason.
const WORKER_SHELF_REASON = 'over plan limit — pick active workers in /settings/billing';

// Distinct accountIds that could possibly hold plan-gated resources. The union keeps the
// sweep bounded: ordinary free accounts (no workers/domains/keys/workspaces/subs) are never
// even loaded. The 90-day subscription window catches recent lapses whose resources may all
// be gone already but whose members/keys still need a restore pass.
async function candidateAccountIds(now: Date): Promise<string[]> {
  const since = new Date(now.getTime() - 90 * 86_400_000);
  const [workers, domains, keys, workspaces, subs] = await Promise.all([
    prisma.worker.findMany({ distinct: ['accountId'], select: { accountId: true } }),
    prisma.anmDomain.findMany({
      where: { status: { in: ['ACTIVE', 'SUSPENDED'] } },
      distinct: ['ownerId'],
      select: { ownerId: true },
    }),
    prisma.apiKey.findMany({ where: { kind: 'production' }, distinct: ['accountId'], select: { accountId: true } }),
    prisma.workspace.findMany({ distinct: ['ownerId'], select: { ownerId: true } }),
    prisma.planSubscription.findMany({ where: { updatedAt: { gt: since } }, distinct: ['accountId'], select: { accountId: true } }),
  ]);
  const ids = new Set<string>();
  for (const r of workers) ids.add(r.accountId);
  for (const r of domains) ids.add(r.ownerId);
  for (const r of keys) ids.add(r.accountId);
  for (const r of workspaces) ids.add(r.ownerId);
  for (const r of subs) ids.add(r.accountId);
  return [...ids];
}

async function enforceAccount(accountId: string, now: Date): Promise<{ shelved: number; restored: number }> {
  const plan = await getAccountPlan(accountId, now);
  const limits = plan.limits;
  let shelved = 0;
  let restored = 0;

  // Workers — a slot is any non-PLAN_LIMIT worker (countWorkerSlots semantics); shelved
  // workers keep their config but the runner never schedules or claims them. Since the
  // Python Cloud catalog un-gated workers (limits.workers is pinned -1 on every tier) the
  // shelve branch is dead code kept for generality: what actually runs now is the restore
  // pass, which un-parks every worker shelved under the OLD paid tiers (to PAUSED, never
  // ACTIVE — the user explicitly starts them again).
  {
    const limit = limits.workers;
    const active = await prisma.worker.findMany({
      where: { accountId, status: { not: 'PLAN_LIMIT' } },
      orderBy: { createdAt: 'asc' },
      select: { id: true },
    });
    if (limit !== -1 && active.length > limit) {
      const res = await prisma.worker.updateMany({
        where: { id: { in: active.slice(limit).map((w) => w.id) } },
        data: { status: 'PLAN_LIMIT', statusReason: WORKER_SHELF_REASON },
      });
      shelved += res.count;
    } else {
      const parked = await prisma.worker.count({ where: { accountId, status: 'PLAN_LIMIT' } });
      if (parked > 0 && (limit === -1 || active.length + parked <= limit)) {
        // Restore to PAUSED, never ACTIVE — the user explicitly starts them again.
        const res = await prisma.worker.updateMany({
          where: { accountId, status: 'PLAN_LIMIT' },
          data: { status: 'PAUSED', statusReason: null },
        });
        restored += res.count;
      }
    }
  }

  // .anm deployments — only ACTIVE occupies a slot; SUSPENDED here always means
  // plan-shelved (the enum's only use), so a full-set restore is safe.
  //
  // GRANDFATHERING: shelving is a DOWNGRADE remedy. An account that never subscribed never
  // downgraded, and its domains were paid for in ANM long before tiers existed — taking those
  // sites offline on the first sweep would be a silent, paid-for-content regression. Accounts
  // with no subscription history are therefore never shelved here; the forward-looking gate in
  // POST /api/mkt/v1/names (requireCanCreateDeployment) still caps NEW registrations.
  const neverSubscribed = plan.subscription === null;
  {
    const limit = limits.anm_deployments;
    const active = await prisma.anmDomain.findMany({
      where: { ownerId: accountId, status: 'ACTIVE' },
      orderBy: { registeredAt: 'asc' },
      select: { id: true },
    });
    if (!neverSubscribed && limit !== -1 && active.length > limit) {
      const res = await prisma.anmDomain.updateMany({
        where: { id: { in: active.slice(limit).map((d) => d.id) } },
        data: { status: 'SUSPENDED' },
      });
      shelved += res.count;
    } else {
      const parked = await prisma.anmDomain.count({ where: { ownerId: accountId, status: 'SUSPENDED' } });
      if (parked > 0 && (limit === -1 || active.length + parked <= limit)) {
        const res = await prisma.anmDomain.updateMany({
          where: { ownerId: accountId, status: 'SUSPENDED' },
          data: { status: 'ACTIVE' },
        });
        restored += res.count;
      }
    }
  }

  // Production API keys — basic keys are never plan-gated; REVOKED is terminal and
  // untouched. SUSPENDED keys stop resolving (resolveApiKey rejects != ACTIVE).
  {
    const limit = limits.api_keys;
    const active = await prisma.apiKey.findMany({
      where: { accountId, kind: 'production', status: 'ACTIVE' },
      orderBy: { createdAt: 'asc' },
      select: { id: true },
    });
    if (limit !== -1 && active.length > limit) {
      const res = await prisma.apiKey.updateMany({
        where: { id: { in: active.slice(limit).map((k) => k.id) } },
        data: { status: 'SUSPENDED' },
      });
      shelved += res.count;
    } else {
      const parked = await prisma.apiKey.count({ where: { accountId, kind: 'production', status: 'SUSPENDED' } });
      if (parked > 0 && (limit === -1 || active.length + parked <= limit)) {
        const res = await prisma.apiKey.updateMany({
          where: { accountId, kind: 'production', status: 'SUSPENDED' },
          data: { status: 'ACTIVE' },
        });
        restored += res.count;
      }
    }
  }

  // Team members — team_members is a PER-WORKSPACE seat limit on the OWNER's plan, so each
  // owned workspace is enforced independently. The OWNER row is never touched. Workspaces
  // beyond the `workspaces` limit keep their data (no workspace shelving); the account-level
  // worker shelving above already covers workers parked inside them.
  {
    const limit = limits.team_members;
    const owned = await prisma.workspace.findMany({ where: { ownerId: accountId }, select: { id: true } });
    for (const ws of owned) {
      const active = await prisma.workspaceMember.findMany({
        where: { workspaceId: ws.id, role: { not: 'OWNER' }, status: 'active' },
        orderBy: { createdAt: 'asc' },
        select: { id: true },
      });
      if (limit !== -1 && active.length > limit) {
        const res = await prisma.workspaceMember.updateMany({
          where: { id: { in: active.slice(limit).map((m) => m.id) } },
          data: { status: 'inactive_plan_limit' },
        });
        shelved += res.count;
      } else {
        const parked = await prisma.workspaceMember.count({
          where: { workspaceId: ws.id, role: { not: 'OWNER' }, status: 'inactive_plan_limit' },
        });
        if (parked > 0 && (limit === -1 || active.length + parked <= limit)) {
          const res = await prisma.workspaceMember.updateMany({
            where: { workspaceId: ws.id, role: { not: 'OWNER' }, status: 'inactive_plan_limit' },
            data: { status: 'active' },
          });
          restored += res.count;
        }
      }
    }
  }

  return { shelved, restored };
}

// One enforcement sweep. Idempotent: a second run over unchanged state changes nothing.
export async function runPlanEnforcement(now: Date = new Date()): Promise<{ suspendedSubs: number; shelved: number; restored: number }> {
  // Grace-lapse first, so the shelving pass below already sees the account at free limits.
  const lapsed = await prisma.planSubscription.updateMany({
    where: { status: { in: ['PAST_DUE', 'GRACE_PERIOD'] }, graceUntil: { lt: now } },
    data: { status: 'SUSPENDED', lastError: 'grace period lapsed without a successful payment' },
  });

  let shelved = 0;
  let restored = 0;
  for (const accountId of await candidateAccountIds(now)) {
    try {
      const r = await enforceAccount(accountId, now);
      shelved += r.shelved;
      restored += r.restored;
    } catch (e) {
      // One bad account must never abort the sweep — the runner calls this every tick.
      console.error(`[planEnforce] account ${accountId} failed:`, e instanceof Error ? e.message : e);
    }
  }
  return { suspendedSubs: lapsed.count, shelved, restored };
}
