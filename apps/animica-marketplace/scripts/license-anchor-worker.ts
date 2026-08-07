import { execFile } from 'node:child_process';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { Prisma } from '@prisma/client';
import { prisma } from '../lib/db';
import { config } from '../lib/config';
import { getHead, getTransaction } from '../lib/chain';
import {
  buildLicenseAnchorPayload, buildLicenseMerkle, licenseLeafHash, parseLicenseAnchorPayload,
} from '../lib/license';
import { acquireAdvisoryLock, makeLogger, normalizeHex, parseFlags } from './store-worker-util';

const pexec = promisify(execFile);

// License anchor worker — batches unanchored License rows into a Merkle checkpoint
// (LicenseAnchor, clone of the DomainAnchor pattern) and publishes the root on-chain as an
// ANMLIC1 data-tx: a 1-nANM self-transfer from the store treasury, posted by
// scripts/post_anchor_tx.py which reuses the EXACT `animica tx send` pipeline the way
// python/animica/cli/settle.py prepare_anchor_tx does (the CLI has no --data flag; the
// settle.py _build_tx_body(data=...) plumbing is the proven recipe). To every node the tx
// is inert opaque data — zero consensus change, tamper-evident via the live txsRoot.
//
// State machine (strictly serialized — the hash chain demands ordered txids):
//   1. any anchor 'published'   => confirm it (depth >= 12 + payload read-back must match)
//   2. else any anchor 'pending'=> post it (STORE_ANCHOR_POST=1 gate, default OFF = dry-run)
//   3. else                     => batch unanchored licenses into a new pending anchor +
//                                  per-license inclusion proofs (STORE_ANCHOR_ENABLED=1
//                                  gate, default OFF = dry-run)
// Each run advances at most one state — a 10-min timer converges within ~3 runs per batch.
//
// Ops: deploy/systemd/animica-store-license-anchor.* (flock; FILES ONLY — orchestrator
// installs/enables). Money note: posting spends 1 nANM + fee from the store treasury; the
// PAYOUT_ENABLED-style gate for that spend is STORE_ANCHOR_POST (default OFF).

const WORKER = 'store-license-anchor';
const log = makeLogger(WORKER);

const ENABLED = process.env.STORE_ANCHOR_ENABLED === '1'; // batch creation (DB writes)
const POST_ENABLED = process.env.STORE_ANCHOR_POST === '1'; // on-chain tx posting (spends from treasury)
const MAX_BATCH = Number(process.env.STORE_ANCHOR_MAX_BATCH ?? 500);
const MIN_LICENSES = Number(process.env.STORE_ANCHOR_MIN_LICENSES ?? 1);
const PYTHON = process.env.STORE_ANCHOR_PYTHON ?? '/root/animica/.venv/bin/python';
const POST_SCRIPT = join(process.cwd(), 'scripts', 'post_anchor_tx.py');

// 1. Confirm a published anchor once buried >= finalityConfs, verifying the on-chain bytes
// actually carry OUR root (read-back check, fail-closed: mismatch flags the operator).
async function confirmPublished(dryRun: boolean): Promise<boolean> {
  const anchor = await prisma.licenseAnchor.findFirst({ where: { status: 'published' }, orderBy: { seq: 'asc' } });
  if (!anchor) return false;

  const tx = anchor.txid ? await getTransaction(anchor.txid) : null;
  if (!tx) {
    const ageMs = Date.now() - new Date(anchor.createdAt).getTime();
    if (ageMs > 24 * 3600_000) {
      log('critical', 'published_anchor_tx_missing', { seq: anchor.seq, txid: anchor.txid, detail: 'posted tx not found after 24h (reorg/evict?) — operator must re-post or reset', adminAttention: true });
    } else {
      log('info', 'published_anchor_waiting_visibility', { seq: anchor.seq, txid: anchor.txid });
    }
    return true;
  }
  const blockNumber = tx.blockNumber != null ? Number(tx.blockNumber) : null;
  if (blockNumber === null) {
    log('info', 'published_anchor_waiting_inclusion', { seq: anchor.seq, txid: anchor.txid });
    return true;
  }
  const head = await getHead();
  const confs = head.height - blockNumber;
  if (confs < config.finalityConfs) {
    log('info', 'published_anchor_waiting_finality', { seq: anchor.seq, txid: anchor.txid, confs, need: config.finalityConfs });
    return true;
  }

  const dataHex = normalizeHex(tx.data);
  const parsed = dataHex ? parseLicenseAnchorPayload(new Uint8Array(Buffer.from(dataHex, 'hex'))) : { ok: false as const, reason: 'no data' };
  if (!parsed.ok || parsed.anchor.root !== anchor.merkleRoot || parsed.anchor.seq !== anchor.seq) {
    log('critical', 'published_anchor_readback_mismatch', {
      seq: anchor.seq, txid: anchor.txid,
      reason: parsed.ok ? 'root/seq mismatch' : parsed.reason,
      adminAttention: true,
    });
    return true; // never auto-confirm bytes we can't verify
  }

  if (dryRun) {
    log('info', 'would_confirm_anchor', { seq: anchor.seq, txid: anchor.txid, blockHeight: blockNumber });
    return true;
  }
  await prisma.licenseAnchor.update({
    where: { id: anchor.id },
    data: { status: 'confirmed', blockHeight: blockNumber, confirmedAt: new Date() },
  });
  log('info', 'anchor_confirmed', { seq: anchor.seq, txid: anchor.txid, blockHeight: blockNumber, leafCount: anchor.leafCount });
  return true;
}

// 2. Post a pending anchor's ANMLIC1 tx via the python helper (dry-runs print the payload).
async function postPending(dryRun: boolean): Promise<boolean> {
  const anchor = await prisma.licenseAnchor.findFirst({ where: { status: 'pending' }, orderBy: { seq: 'asc' } });
  if (!anchor) return false;

  // Sanity: the payload must build cleanly (same strict encoder the parser accepts).
  buildLicenseAnchorPayload({ seq: anchor.seq, root: anchor.merkleRoot, prev: anchor.prevTxid, n: anchor.leafCount });

  if (dryRun || !POST_ENABLED) {
    log('info', 'would_post_anchor', { seq: anchor.seq, root: anchor.merkleRoot, prev: anchor.prevTxid, leafCount: anchor.leafCount, gate: POST_ENABLED ? 'dry-run' : 'STORE_ANCHOR_POST!=1' });
    return true;
  }
  if (!config.storeTreasuryAddress) {
    log('error', 'store_treasury_unconfigured', { detail: 'STORE_TREASURY_ADDRESS unset — cannot post anchors' });
    return true;
  }

  const args = [
    POST_SCRIPT,
    '--from-address', config.storeTreasuryAddress,
    '--seq', String(anchor.seq),
    '--root', anchor.merkleRoot,
    '--prev', anchor.prevTxid ?? '',
    '--count', String(anchor.leafCount),
    '--rpc-url', config.rpcUrl,
    '--post',
  ];
  log('info', 'posting_anchor', { seq: anchor.seq, root: anchor.merkleRoot, leafCount: anchor.leafCount });
  const { stdout } = await pexec(PYTHON, args, {
    env: { ...process.env, ANIMICA_WALLETS_FILE: config.walletsFile },
    timeout: 120_000,
  });
  const lastLine = stdout.trim().split('\n').pop() ?? '';
  let result: any;
  try {
    result = JSON.parse(lastLine);
  } catch {
    throw new Error(`post_anchor_tx.py output not JSON: ${stdout.slice(0, 400)}`);
  }
  if (!result.ok || typeof result.txid !== 'string') {
    throw new Error(`post_anchor_tx.py failed: ${result.error ?? lastLine.slice(0, 400)}`);
  }
  await prisma.licenseAnchor.update({ where: { id: anchor.id }, data: { status: 'published', txid: result.txid } });
  log('info', 'anchor_posted', { seq: anchor.seq, txid: result.txid });
  return true;
}

// 3. Batch unanchored licenses into a new pending anchor + inclusion proofs (one tx).
async function createBatch(dryRun: boolean): Promise<void> {
  const licenses = await prisma.license.findMany({
    where: { anchorId: null, leafHash: { not: '' } },
    include: { purchase: { select: { txid: true } } },
    orderBy: { issuedAt: 'asc' },
    take: MAX_BATCH,
  });
  if (licenses.length < MIN_LICENSES) {
    log('info', 'no_batch', { unanchored: licenses.length, min: MIN_LICENSES });
    return;
  }

  // Verify every stored leafHash against a recompute — a corrupted leaf must never enter
  // a published root (the wallet would recompute and see a proof that verifies a lie).
  const good: { id: string; leafHash: string }[] = [];
  for (const lic of licenses) {
    const recomputed = licenseLeafHash({
      licenseId: lic.id,
      purchaseTxid: lic.purchase?.txid ?? null,
      buyerAddress: lic.buyerAddress,
      listingId: lic.listingId,
      buildId: lic.buildId,
      buildSha3: lic.buildSha3,
      certSha256: lic.certSha256,
      kind: lic.kind,
      issuedAt: lic.issuedAt,
      expiresAt: lic.expiresAt,
    });
    if (recomputed !== lic.leafHash) {
      log('critical', 'leaf_hash_mismatch', { licenseId: lic.id, stored: lic.leafHash, recomputed, adminAttention: true });
      continue;
    }
    good.push({ id: lic.id, leafHash: lic.leafHash });
  }
  if (good.length < MIN_LICENSES) {
    log('warn', 'batch_below_min_after_verification', { good: good.length, min: MIN_LICENSES });
    return;
  }

  const { root, proofs } = buildLicenseMerkle(good.map((l) => l.leafHash));
  const prev = await prisma.licenseAnchor.findFirst({ orderBy: { seq: 'desc' } });
  const seq = (prev?.seq ?? 0) + 1;
  const prevTxid = prev?.txid ?? null;
  // Strict pre-validation — the exact bytes we will later post must encode+parse cleanly.
  buildLicenseAnchorPayload({ seq, root, prev: prevTxid, n: good.length });

  if (dryRun || !ENABLED) {
    log('info', 'would_create_anchor', { seq, root, prevTxid, leafCount: good.length, gate: ENABLED ? 'dry-run' : 'STORE_ANCHOR_ENABLED!=1' });
    return;
  }

  await prisma.$transaction(async (tx) => {
    const anchor = await tx.licenseAnchor.create({
      data: { seq, merkleRoot: root, leafCount: good.length, prevTxid, status: 'pending' },
    });
    for (const lic of good) {
      const claimed = await tx.license.updateMany({
        where: { id: lic.id, anchorId: null },
        data: {
          anchorId: anchor.id,
          merkleProofJson: { leafHash: lic.leafHash, root, seq, steps: proofs[lic.leafHash] } as unknown as Prisma.InputJsonValue,
        },
      });
      if (claimed.count !== 1) throw new Error(`license ${lic.id} concurrently anchored — aborting batch`);
    }
  });
  log('info', 'anchor_created', { seq, root, prevTxid, leafCount: good.length });
}

async function main() {
  const { dryRun } = parseFlags();
  if (!(await acquireAdvisoryLock(WORKER))) {
    log('info', 'another_instance_running', {});
    return;
  }
  log('info', 'run_start', { dryRun, enabled: ENABLED, postEnabled: POST_ENABLED });

  if (await confirmPublished(dryRun)) {
    log('info', 'run_done', { stage: 'confirm' });
    return;
  }
  if (await postPending(dryRun)) {
    log('info', 'run_done', { stage: 'post' });
    return;
  }
  await createBatch(dryRun);
  log('info', 'run_done', { stage: 'batch' });
}

main()
  .catch((e) => {
    log('error', 'run_crashed', { error: String(e?.stack ?? e) });
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
