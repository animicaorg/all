import { prisma } from '../lib/db';
import { config } from '../lib/config';
import { getAddressBalance, getHead } from '../lib/chain';
import { scanDepositAddress } from '../lib/deposit';
import { acquireAdvisoryLock, makeLogger, parseFlags, sleep } from './store-worker-util';

// Deposit watcher — background crediting of on-chain deposits. Until now deposits were
// scanned ONLY when the user polled GET /api/mkt/v1/deposits/address; store top-ups (and
// especially subscription dunning: "add funds and the renewal just works") need credits to
// land without the user sitting on the page. This was declared in package.json
// (worker:deposits) since 8.0.0 but never built.
//
// All verification stays in lib/deposit.ts scanDepositAddress — observed BALANCE DELTA
// beyond baseline+alreadyCredited, after finality (inclusion != execution on this chain);
// this worker only iterates the active DepositAddress rows and reports. Idempotent by
// construction (creditedNanm bookkeeping), and safe alongside the on-demand route scan.
//
// Ops: deploy/systemd/animica-store-deposits.* (flock + advisory lock; FILES ONLY —
// orchestrator installs/enables). Gate: DEPOSIT_WATCHER_ENABLED=1 arms it; default OFF =
// observe-only dry run. --dry-run / DRY_RUN=1 forces observe-only.

const WORKER = 'store-deposits';
const log = makeLogger(WORKER);

const ENABLED = process.env.DEPOSIT_WATCHER_ENABLED === '1';
const BATCH = Number(process.env.DEPOSIT_WATCHER_BATCH ?? 200);
const RPC_PAUSE_MS = Number(process.env.DEPOSIT_WATCHER_PAUSE_MS ?? 100);

async function main() {
  const { dryRun: flagDryRun } = parseFlags();
  const dryRun = flagDryRun || !ENABLED;

  if (!(await acquireAdvisoryLock(WORKER))) {
    log('info', 'another_instance_running', {});
    return;
  }
  const head = await getHead().catch(() => null);
  log('info', 'run_start', { dryRun, enabled: ENABLED, headHeight: head?.height ?? null });

  const addresses = await prisma.depositAddress.findMany({
    where: { active: true },
    orderBy: { lastSeenHeight: 'asc' }, // least-recently-scanned first
    select: {
      id: true, address: true, accountId: true, purpose: true,
      baselineNanm: true, creditedNanm: true, pendingNanm: true, pendingSinceHeight: true,
    },
    take: BATCH,
  });

  let credited = 0n, creditedCount = 0, errors = 0;
  for (const rec of addresses) {
    try {
      if (dryRun) {
        // Observe-only projection of scanDepositAddress's finality gate. We DO NOT write the
        // pending window in dry-run, so we can only report the observed uncredited delta and, if a
        // window is already active from a prior armed run, whether it has matured. Never claims a
        // credit that the armed worker would not actually make.
        const { nanm: observed } = await getAddressBalance(rec.address);
        const uncredited = observed - rec.baselineNanm - rec.creditedNanm;
        if (uncredited > 0n) {
          const confs = rec.pendingSinceHeight > 0 && head ? head.height - rec.pendingSinceHeight : null;
          const matured = rec.pendingNanm > 0n && uncredited >= rec.pendingNanm && confs !== null && confs >= config.finalityConfs;
          if (matured) {
            log('info', 'would_credit_deposit', { depositAddrId: rec.id, accountId: rec.accountId, purpose: rec.purpose, creditableNanm: rec.pendingNanm, confs });
            credited += rec.pendingNanm;
            creditedCount += 1;
          } else {
            log('info', 'deposit_pending_finality', {
              depositAddrId: rec.id, accountId: rec.accountId, purpose: rec.purpose,
              uncreditedNanm: uncredited, pendingNanm: rec.pendingNanm, confs, needConfs: config.finalityConfs,
            });
          }
        }
      } else {
        const res = await scanDepositAddress(rec.id);
        if (res.credited > 0n) {
          credited += res.credited;
          creditedCount += 1;
          log('info', 'deposit_credited', { depositAddrId: rec.id, accountId: rec.accountId, purpose: rec.purpose, creditedNanm: res.credited });
        }
      }
    } catch (e: any) {
      errors += 1;
      log('warn', 'scan_error', { depositAddrId: rec.id, error: String(e?.message ?? e) });
    }
    if (RPC_PAUSE_MS > 0) await sleep(RPC_PAUSE_MS); // pace the node (RPC starvation gotcha)
  }
  log('info', 'run_done', { scanned: addresses.length, creditedCount, creditedNanm: credited, errors });
}

main()
  .catch((e) => {
    log('error', 'run_crashed', { error: String(e?.stack ?? e) });
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
