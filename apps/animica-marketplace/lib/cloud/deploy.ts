// Animica Python Cloud — the deployment pipeline (§5, §6, §61).
//
// Lifecycle, persisted at every step so the console can show a truthful timeline:
//
//   DRAFT → VALIDATING → BUILDING → AWAITING_SIGNATURE → BROADCASTING → CONFIRMING → ACTIVE
//                                                                                  ↘ FAILED
//
// Anchoring truth (verified, see lib/cloud/anchor.ts): deployments are ANCHORED on-chain via a
// DEPLOY (t=1) transaction + a DA blob, and EXECUTED off-chain by the sandbox fleet. Consensus
// never executes the Python — vm_py CALLs revert on mainnet by design.
//
// Version history is APPEND-ONLY (§6): CloudFunctionVersion rows are immutable snapshots, a
// redeploy creates version max+1, and a rollback creates a NEW deployment pointing at an OLD
// version row. Nothing ever rewrites what was previously deployed.

import { createHash } from 'node:crypto';
import { prisma } from '../db';
import { ApiError } from '../api';
import { config } from '../config';
import { getAddressBalance } from '../chain';
import { flags, limits, runtime } from './config';
import { activePolicy, estimate } from './pricing';
import { feeBpsForDeveloper, resolvePlan, requireEntitlement, consumeQuota, entitlementError, USAGE, type ResolvedPlan } from './entitlements';
import { validateSource, isValidatorFault, type ValidationReport } from './validate';
import {
  anchorWallet,
  broadcastAnchor,
  confirmAnchor,
  daPutAnchor,
  type AnchorManifest,
} from './anchor';

// ---------------------------------------------------------------------------
// Canonical artifact + hashes
// ---------------------------------------------------------------------------

// WHAT THE HASHES MEAN (this definition is bound into the on-chain anchor, so it is frozen):
//
//   sourceSha3   = sha3-256(source utf-8) — the verbatim Python text, matching
//                  lib/cloud/sandbox.ts sourceSha3() and the chain's hash family.
//
//   artifactSha3 = sha3-256 of the CANONICAL ARTIFACT MANIFEST: a sorted-key, no-whitespace
//                  JSON document describing exactly the unit the sandbox runs —
//                  {"entrypoint":…,"files":{"handler.py":<sourceSha3>},"packages":[sorted…],
//                   "runtime":…}.
//
// Why a JSON manifest and not a zip: runSandbox() stages EXACTLY one file (handler.py = the
// verbatim source) into the container and invokes <entrypoint> under the runtime image — there
// is no multi-file bundle to zip, and a zip would drag in timestamp/ordering normalization for
// zero benefit. The manifest covers everything that changes what actually executes (source
// bytes via the file map, entrypoint, package set, runtime), is byte-deterministic, and is
// reproducible by anyone from the DA bundle alone.

export function sha3OfUtf8(text: string): string {
  return createHash('sha3-256').update(text, 'utf8').digest('hex');
}

function canonicalJson(value: unknown): string {
  // Deterministic serialization: object keys sorted at every level, arrays kept in order.
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    const keys = Object.keys(value as Record<string, unknown>).sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson((value as any)[k])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

export interface ArtifactInput {
  source: string;
  entrypoint: string;
  runtime: string;
  packages: string[];
}

export function computeArtifact(input: ArtifactInput): {
  sourceSha3: string;
  artifactSha3: string;
  manifestJson: string;
} {
  const sourceSha3 = sha3OfUtf8(input.source);
  const manifestJson = canonicalJson({
    entrypoint: input.entrypoint,
    files: { 'handler.py': sourceSha3 },
    packages: [...input.packages].sort(),
    runtime: input.runtime,
  });
  return { sourceSha3, artifactSha3: sha3OfUtf8(manifestJson), manifestJson };
}

// ---------------------------------------------------------------------------
// Package policy
// ---------------------------------------------------------------------------

// The curated set baked into the sandbox image (sandbox/Dockerfile, §27). The sandbox has no
// network, so anything outside this list simply is not importable — refusing it here turns a
// guaranteed runtime ImportError into a clear deploy-time message. Accepts both the pip name
// and the import name where they differ.
const PACKAGE_ALIASES: Record<string, string> = {
  numpy: 'numpy',
  pandas: 'pandas',
  pyyaml: 'pyyaml',
  yaml: 'pyyaml',
  'python-dateutil': 'python-dateutil',
  dateutil: 'python-dateutil',
  orjson: 'orjson',
  jinja2: 'jinja2',
  markdown: 'markdown',
  beautifulsoup4: 'beautifulsoup4',
  bs4: 'beautifulsoup4',
  pillow: 'pillow',
  pil: 'pillow',
  cryptography: 'cryptography',
  pydantic: 'pydantic',
};
const MAX_PACKAGES = 16;

function normalizePackages(requested: unknown): string[] {
  if (requested == null) return [];
  if (!Array.isArray(requested)) throw new ApiError(400, 'bad_request', 'packages must be an array of names');
  const out = new Set<string>();
  for (const raw of requested) {
    const name = String(raw ?? '').trim().toLowerCase();
    if (!name) continue;
    const canonical = PACKAGE_ALIASES[name];
    if (!canonical) {
      throw new ApiError(
        422,
        'unsupported_package',
        `package "${name}" is not in the sandbox runtime. Available: ${[...new Set(Object.values(PACKAGE_ALIASES))].sort().join(', ')} (plus the Python standard library).`,
      );
    }
    out.add(canonical);
    if (out.size > MAX_PACKAGES) throw new ApiError(422, 'too_many_packages', `at most ${MAX_PACKAGES} packages per function`);
  }
  return [...out].sort();
}

// ---------------------------------------------------------------------------
// Deployment log — persisted to CloudDeployment.logsJson at every transition
// ---------------------------------------------------------------------------

interface DeployLogLine {
  ts: string;
  level: 'info' | 'warn' | 'error';
  message: string;
}

const MAX_DEPLOY_LOG_LINES = 200;

function parseLogs(json: string): DeployLogLine[] {
  try {
    const a = JSON.parse(json || '[]');
    return Array.isArray(a) ? a : [];
  } catch {
    return [];
  }
}

/** Set the deployment status and append log lines in one write, so a crash between steps can
 *  never leave a status the log does not explain. */
async function transition(
  deploymentId: string,
  status:
    | 'DRAFT'
    | 'VALIDATING'
    | 'BUILDING'
    | 'AWAITING_SIGNATURE'
    | 'BROADCASTING'
    | 'CONFIRMING'
    | 'ACTIVE'
    | 'FAILED'
    | null,
  lines: Array<{ level?: 'info' | 'warn' | 'error'; message: string }>,
  extra: Record<string, unknown> = {},
) {
  const row = await prisma.cloudDeployment.findUnique({ where: { id: deploymentId }, select: { logsJson: true } });
  const logs = parseLogs(row?.logsJson ?? '[]');
  for (const l of lines) {
    logs.push({ ts: new Date().toISOString(), level: l.level ?? 'info', message: l.message.slice(0, 500) });
  }
  await prisma.cloudDeployment.update({
    where: { id: deploymentId },
    data: {
      ...(status ? { status: status as any } : {}),
      logsJson: JSON.stringify(logs.slice(-MAX_DEPLOY_LOG_LINES)),
      ...extra,
    },
  });
}

// ---------------------------------------------------------------------------
// Shared guards
// ---------------------------------------------------------------------------

async function denylistHit(hashes: string[]): Promise<{ sha3: string; reason: string } | null> {
  const rows = await prisma.cloudCodeDenylist.findMany({ where: { sha3: { in: hashes } } });
  return rows.length ? { sha3: rows[0].sha3, reason: rows[0].reason } : null;
}

/** Live COUNT of the owner's deployments started since UTC midnight — the per-day gate is a
 *  real query, never a counter (§92). */
async function deploysToday(ownerId: string): Promise<number> {
  const now = new Date();
  const dayStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  return prisma.cloudDeployment.count({
    where: { createdAt: { gte: dayStart }, function: { ownerId } },
  });
}

async function enforceDeployQuota(ownerId: string, plan: ResolvedPlan) {
  const limit = plan.limits.max_deployments_per_day;
  if (typeof limit === 'number' && limit !== -1) {
    const used = await deploysToday(ownerId);
    if (used >= limit) {
      throw entitlementError('max_deployments_per_day', {
        plan: plan.key,
        limit,
        used,
        needed: used + 1,
        message: `You have used ${used} of ${limit} deployments today on the ${plan.key} plan. The window resets at 00:00 UTC.`,
      });
    }
  }
  // Record the monthly cloud_deploys usage counter (analytics + admin console). The enforced
  // gate is the live daily COUNT above; this counter is bookkeeping and must not block.
  await consumeQuota(ownerId, USAGE.deploys, 1, plan).catch(() => {});
}

/** A deploy that would PUBLISH a not-yet-published function occupies a plan slot; redeploys of
 *  an already-published function never do (existing resources keep running on downgrade). */
async function enforceFunctionSlot(fn: { id: string; ownerId: string; status: string }, plan: ResolvedPlan) {
  if (fn.status === 'PUBLISHED') return;
  const publishedCount = await prisma.cloudFunction.count({
    where: { ownerId: fn.ownerId, status: 'PUBLISHED', NOT: { id: fn.id } },
  });
  const limit = plan.limits.max_functions;
  if (typeof limit === 'number' && limit !== -1 && publishedCount >= limit) {
    throw entitlementError('max_functions', {
      plan: plan.key,
      limit,
      used: publishedCount,
      needed: publishedCount + 1,
      message: `You have ${publishedCount} of ${limit} functions live on the ${plan.key} plan. Upgrade at /cloud/pricing.`,
    });
  }
}

function ownerSegment(owner: { handle: string | null; address: string }): string {
  return owner.handle ?? owner.address;
}

function endpointFor(owner: { handle: string | null; address: string }, slug: string): string {
  return `/api/cloud/v1/fn/${ownerSegment(owner)}/${slug}`;
}

// ---------------------------------------------------------------------------
// createVersion — snapshot new source as version max+1 and deploy it
// ---------------------------------------------------------------------------

export interface CreateVersionInput {
  functionId: string;
  source: string;
  entrypoint?: string;
  packages?: string[];
  actorAccountId: string;
}

export interface CreateVersionResult {
  functionId: string;
  versionId: string;
  version: number;
  deploymentId: string;
  status: string;
  endpoint: string | null;
  anchorTxid: string | null;
  daBlobId: string | null;
  validation: ValidationReport;
  estimateNanm: bigint;
}

export async function createVersion(input: CreateVersionInput): Promise<CreateVersionResult> {
  if (!flags.pythonCloud) throw new ApiError(503, 'disabled', 'Python Cloud deployments are temporarily disabled');

  const fn = await prisma.cloudFunction.findUnique({
    where: { id: input.functionId },
    include: { owner: { select: { id: true, address: true, handle: true } } },
  });
  if (!fn) throw new ApiError(404, 'not_found', 'function not found');
  if (fn.ownerId !== input.actorAccountId) throw new ApiError(403, 'forbidden', 'only the function owner can deploy');
  if (fn.suspendedAt) throw new ApiError(403, 'suspended', fn.suspendedReason || 'this function has been suspended');

  const source = String(input.source ?? '');
  const sizeBytes = Buffer.byteLength(source, 'utf8');
  if (sizeBytes === 0) throw new ApiError(400, 'bad_request', 'source is empty');
  if (sizeBytes > limits.maxSourceBytes) {
    throw new ApiError(413, 'too_large', `source is ${sizeBytes} bytes; the limit is ${limits.maxSourceBytes}`);
  }
  const entrypoint = (input.entrypoint || fn.entrypoint || 'main').trim();
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(entrypoint)) {
    throw new ApiError(400, 'bad_request', 'entrypoint must be a valid Python identifier');
  }
  const packages = normalizePackages(input.packages);

  // --- entitlements, all against live counts ---------------------------------------------
  const plan = await resolvePlan(fn.ownerId);
  if (fn.visibility === 'PUBLIC') await requireEntitlement(fn.ownerId, 'marketplace_publishing', 1, plan);
  await enforceFunctionSlot(fn, plan);
  await enforceDeployQuota(fn.ownerId, plan);

  // --- static validation (fail closed on validator faults) -------------------------------
  const validation = await validateSource({ source, entrypoint });
  if (isValidatorFault(validation)) {
    throw new ApiError(503, 'validator_unavailable', validation.findings[0]?.message ?? 'the validator is unavailable');
  }
  if (!validation.ok) {
    const e = new ApiError(422, 'validation_failed', 'the source failed pre-deploy validation');
    e.details = { findings: validation.findings };
    throw e;
  }

  // --- hashes + denylist ------------------------------------------------------------------
  const { sourceSha3, artifactSha3 } = computeArtifact({ source, entrypoint, runtime: fn.runtime, packages });
  const denied = await denylistHit([artifactSha3, sourceSha3]);
  if (denied) {
    throw new ApiError(403, 'code_denied', `this code fingerprint has been blocked by the platform: ${denied.reason}`);
  }

  // --- per-call estimate shown at deploy time ---------------------------------------------
  const policy = await activePolicy();
  const feeBps = await feeBpsForDeveloper(fn.ownerId, policy.platformFeeBps);
  const est = estimate({ timeoutMs: fn.timeoutMs, memoryMb: fn.memoryMb, surchargeNanm: fn.perCallNanm, feeBps }, policy);

  // --- snapshot: version = max+1, never overwritten (§6). The @@unique([functionId,version])
  // constraint turns a concurrent-deploy race into a clean retryable conflict. -------------
  const { versionRow, deploymentRow } = await prisma
    .$transaction(async (tx) => {
      const latest = await tx.cloudFunctionVersion.findFirst({
        where: { functionId: fn.id },
        orderBy: { version: 'desc' },
        select: { version: true },
      });
      const versionRow = await tx.cloudFunctionVersion.create({
        data: {
          functionId: fn.id,
          version: (latest?.version ?? 0) + 1,
          source,
          sourceSha3,
          artifactSha3,
          sizeBytes,
          entrypoint,
          packages,
          validationJson: JSON.stringify(validation),
          estimateNanm: est.typicalNanm,
          createdById: input.actorAccountId,
        },
      });
      const deploymentRow = await tx.cloudDeployment.create({
        data: { functionId: fn.id, versionId: versionRow.id, status: 'DRAFT', logsJson: '[]' },
      });
      return { versionRow, deploymentRow };
    })
    .catch((e: any) => {
      if (e?.code === 'P2002') throw new ApiError(409, 'conflict', 'another deployment claimed this version number; retry');
      throw e;
    });

  const finished = await deployVersion(deploymentRow.id);
  return {
    functionId: fn.id,
    versionId: versionRow.id,
    version: versionRow.version,
    deploymentId: finished.id,
    status: finished.status,
    endpoint: finished.endpoint,
    anchorTxid: finished.anchorTxid,
    daBlobId: finished.daBlobId,
    validation,
    estimateNanm: est.typicalNanm,
  };
}

// ---------------------------------------------------------------------------
// deployVersion — drive (or resume) one deployment through the lifecycle
// ---------------------------------------------------------------------------

export async function deployVersion(deploymentId: string) {
  const dep = await prisma.cloudDeployment.findUnique({
    where: { id: deploymentId },
    include: {
      version: true,
      function: { include: { owner: { select: { id: true, address: true, handle: true } } } },
    },
  });
  if (!dep) throw new ApiError(404, 'not_found', 'deployment not found');
  if (dep.status === 'ACTIVE') return dep; // idempotent resume
  if (dep.status === 'ARCHIVED') throw new ApiError(409, 'archived', 'this deployment has been archived');

  const fn = dep.function;
  const ver = dep.version;

  const fail = async (message: string, code = 'deploy_failed', httpStatus = 422): Promise<never> => {
    await transition(dep.id, 'FAILED', [{ level: 'error', message }], { error: message.slice(0, 1000) });
    const e = new ApiError(httpStatus, code, message);
    e.details = { deploymentId: dep.id };
    throw e;
  };

  // ---- VALIDATING -------------------------------------------------------------------------
  // Re-run even when createVersion just validated: deployVersion is also the RESUME path for
  // old DRAFT/FAILED rows, and the denylist may have grown since the row was created.
  await transition(dep.id, 'VALIDATING', [{ message: `validating version ${ver.version} (${ver.sizeBytes} bytes, entrypoint ${ver.entrypoint})` }]);
  const validation = await validateSource({ source: ver.source, entrypoint: ver.entrypoint });
  if (isValidatorFault(validation)) {
    return fail(validation.findings[0]?.message ?? 'the validator is unavailable', 'validator_unavailable', 503);
  }
  if (!validation.ok) {
    const first = validation.findings.find((f) => f.severity === 'error');
    return fail(`validation failed: ${first?.message ?? 'see findings'}${first?.line ? ` (line ${first.line})` : ''}`, 'validation_failed');
  }

  // Immutability check: recompute the hashes from the stored snapshot and require them to match
  // what was recorded at creation. A mismatch means the "immutable" row was altered — refuse to
  // anchor a lie.
  const recomputed = computeArtifact({
    source: ver.source,
    entrypoint: ver.entrypoint,
    runtime: fn.runtime,
    packages: ver.packages,
  });
  if (recomputed.sourceSha3 !== ver.sourceSha3 || recomputed.artifactSha3 !== ver.artifactSha3) {
    return fail('stored version hashes do not match its source snapshot — refusing to deploy a tampered version', 'integrity_error', 409);
  }
  const denied = await denylistHit([ver.artifactSha3, ver.sourceSha3]);
  if (denied) return fail(`this code fingerprint has been blocked by the platform: ${denied.reason}`, 'code_denied', 403);

  // ---- BUILDING ---------------------------------------------------------------------------
  // The build product is the DA anchor bundle: canonical artifact manifest + verbatim source,
  // enough for ANYONE holding the blob id to re-derive both hashes and audit the deployment.
  await transition(dep.id, 'BUILDING', [{ message: 'building canonical artifact bundle' }]);
  let daBlobId: string | null = dep.daBlobId;
  if (flags.anchoring) {
    const bundle = Buffer.from(
      JSON.stringify({
        kind: 'animica.pythoncloud.v1/bundle',
        artifact: JSON.parse(recomputed.manifestJson),
        sourceSha3: ver.sourceSha3,
        artifactSha3: ver.artifactSha3,
        source: ver.source,
      }),
      'utf8',
    );
    try {
      // Content-addressed: resuming a deployment re-puts the identical bundle and gets the
      // identical blob id back, so this step is naturally idempotent.
      const put = await daPutAnchor(bundle, {
        contentType: 'application/json',
        owner: fn.owner.address,
        tags: ['pycloud', `fn:${fn.id}`, `v:${ver.version}`],
      });
      daBlobId = put.blobId;
      await transition(dep.id, null, [{ message: `DA blob stored: ${put.blobId} (${put.sizeBytes} bytes)` }], { daBlobId });
    } catch (e: any) {
      // The DA blob is half the anchor; without it the DEPLOY manifest would bind a blob id
      // that does not exist. Anchor honestly as "unanchored" rather than half-anchor.
      daBlobId = null;
      await transition(dep.id, null, [
        { level: 'warn', message: `DA put failed (${String(e?.message ?? e).slice(0, 200)}); continuing unanchored` },
      ]);
    }
  } else {
    await transition(dep.id, null, [{ level: 'warn', message: 'on-chain anchoring is disabled by the operator (CLOUD_ANCHOR_ENABLED=0); deploying unanchored' }]);
  }

  // ---- AWAITING_SIGNATURE / BROADCASTING --------------------------------------------------
  let anchorTxid: string | null = dep.anchorTxid;
  let deployerAddress: string | null = dep.deployerAddress;
  if (flags.anchoring && daBlobId && !anchorTxid) {
    await transition(dep.id, 'AWAITING_SIGNATURE', [{ message: `resolving anchor wallet "${runtime.anchorWalletLabel}"` }]);
    const manifest: AnchorManifest = {
      kind: 'animica.pythoncloud.v1',
      owner: fn.owner.address,
      function: `${ownerSegment(fn.owner)}/${fn.slug}`,
      version: ver.version,
      sourceSha3: ver.sourceSha3,
      artifactSha3: ver.artifactSha3,
      daBlobId,
      ts: Date.now(),
    };
    await transition(dep.id, 'BROADCASTING', [{ message: 'signing and broadcasting the anchor DEPLOY (t=1) transaction' }]);
    const b = await broadcastAnchor(manifest);
    if (b.broadcast && b.txid) {
      anchorTxid = b.txid;
      deployerAddress = b.deployerAddress;
      await transition(dep.id, null, [{ message: `anchor tx broadcast: ${b.txid}` }], { anchorTxid, deployerAddress });
    } else {
      // NEVER fabricate a txid (§61): record the real reason and continue to ACTIVE unanchored.
      deployerAddress = b.deployerAddress;
      await transition(dep.id, null, [{ level: 'warn', message: b.reason ?? 'anchor broadcast did not happen' }], { deployerAddress });
    }
  } else if (anchorTxid) {
    await transition(dep.id, null, [{ message: `resuming with existing anchor tx ${anchorTxid}` }]);
  }

  // ---- CONFIRMING -------------------------------------------------------------------------
  // Bounded wait for INCLUSION (anchorHeight). Full finality is 12 confirmations — minutes of
  // block cadence — so ACTIVE is not held hostage to it; refreshAnchor() keeps updating the
  // recorded confirmation depth afterwards and the UI shows the real number.
  if (anchorTxid) {
    await transition(dep.id, 'CONFIRMING', [{ message: `waiting for anchor inclusion (finality depth ${runtime.anchorConfirmations})` }]);
    const inclusionWaitMs = Number(process.env.CLOUD_ANCHOR_INCLUSION_WAIT_MS ?? '90000');
    const c = await confirmAnchor(anchorTxid, { maxWaitMs: inclusionWaitMs });
    await transition(
      dep.id,
      null,
      [
        c.height != null
          ? { message: `anchor included at height ${c.height} (${c.confirms}/${runtime.anchorConfirmations} confirmations${c.confirmed ? ', final' : ''})` }
          : { level: 'warn', message: `anchor tx not yet included after ${inclusionWaitMs}ms; confirmation tracking continues in the background` },
      ],
      { anchorHeight: c.height, anchorConfirms: c.confirms },
    );
  }

  // ---- ACTIVE -----------------------------------------------------------------------------
  const endpoint = endpointFor(fn.owner, fn.slug);
  await prisma.$transaction([
    prisma.cloudDeployment.update({
      where: { id: dep.id },
      data: { status: 'ACTIVE', endpoint, activatedAt: new Date(), error: null },
    }),
    prisma.cloudFunction.update({
      where: { id: fn.id },
      data: { status: 'PUBLISHED', currentVersion: ver.version },
    }),
  ]);
  await transition(dep.id, null, [
    { message: `version ${ver.version} is live at ${endpoint}${anchorTxid ? '' : ' (unanchored — see log above for the recorded reason)'}` },
  ]);

  // ---- node function registry (best-effort, §61) ------------------------------------------
  // aicf.fn.deploy is live on the node (verified via rpc.listMethods). Registration makes the
  // function discoverable to node-side tooling, but the registry is NOT the source of truth —
  // a registry failure is logged, never fatal.
  try {
    const registryName = `pycloud/${ownerSegment(fn.owner)}/${fn.slug}`;
    const res = await registryDeploy({
      name: registryName,
      app: 'animica-python-cloud',
      image_ref: runtime.image,
      gpu: null,
      timeout: Math.ceil(fn.timeoutMs / 1000),
      schedule: null,
      entrypoint: `handler:${ver.entrypoint}`,
      pycloud: {
        functionId: fn.id,
        version: ver.version,
        endpoint,
        sourceSha3: ver.sourceSha3,
        artifactSha3: ver.artifactSha3,
        daBlobId,
        anchorTxid,
      },
    });
    if (res?.ok) {
      await transition(dep.id, null, [{ message: `registered with the node function registry as ${res.name} (registry v${res.version})` }], {
        registryName: res.name,
      });
    }
  } catch (e: any) {
    await transition(dep.id, null, [
      { level: 'warn', message: `node registry registration failed (${String(e?.message ?? e).slice(0, 200)}); deployment is unaffected` },
    ]);
  }

  const final = await prisma.cloudDeployment.findUnique({ where: { id: dep.id } });
  return final!;
}

let registryRpcId = 1;
async function registryDeploy(meta: Record<string, unknown>): Promise<{ ok: boolean; name: string; version: number } | null> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 10_000);
  try {
    const res = await fetch(config.rpcUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: registryRpcId++, method: 'aicf.fn.deploy', params: [meta] }),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new Error(`http ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error.message ?? JSON.stringify(data.error));
    return data.result ?? null;
  } finally {
    clearTimeout(t);
  }
}

// ---------------------------------------------------------------------------
// rollbackTo — a NEW deployment of an OLD version (history is never mutated)
// ---------------------------------------------------------------------------

export async function rollbackTo(functionId: string, version: number, actorAccountId: string) {
  const fn = await prisma.cloudFunction.findUnique({
    where: { id: functionId },
    include: { owner: { select: { id: true, address: true, handle: true } } },
  });
  if (!fn) throw new ApiError(404, 'not_found', 'function not found');
  if (fn.ownerId !== actorAccountId) throw new ApiError(403, 'forbidden', 'only the function owner can roll back');
  if (fn.suspendedAt) throw new ApiError(403, 'suspended', fn.suspendedReason || 'this function has been suspended');

  const target = await prisma.cloudFunctionVersion.findUnique({
    where: { functionId_version: { functionId, version } },
  });
  if (!target) throw new ApiError(404, 'not_found', `version ${version} does not exist for this function`);
  if (target.version === fn.currentVersion) {
    throw new ApiError(409, 'noop', `version ${version} is already the current version`);
  }

  // A rollback is a deployment: same daily quota, same slot rules.
  const plan = await resolvePlan(fn.ownerId);
  await enforceFunctionSlot(fn, plan);
  await enforceDeployQuota(fn.ownerId, plan);

  const dep = await prisma.cloudDeployment.create({
    data: {
      functionId,
      versionId: target.id,
      status: 'DRAFT',
      logsJson: JSON.stringify([
        {
          ts: new Date().toISOString(),
          level: 'info',
          message: `rollback: redeploying version ${target.version} (current was ${fn.currentVersion})`,
        },
      ]),
    },
  });
  return deployVersion(dep.id);
}

// ---------------------------------------------------------------------------
// refreshAnchor — keep the recorded confirmation depth truthful after ACTIVE
// ---------------------------------------------------------------------------

/** Single-shot re-check of an anchor tx (UI polling / janitor). Updates anchorHeight and
 *  anchorConfirms from the live chain; a no-op for unanchored deployments. */
export async function refreshAnchor(deploymentId: string) {
  const dep = await prisma.cloudDeployment.findUnique({
    where: { id: deploymentId },
    select: { id: true, anchorTxid: true, anchorHeight: true, anchorConfirms: true },
  });
  if (!dep) throw new ApiError(404, 'not_found', 'deployment not found');
  if (!dep.anchorTxid) return { anchorTxid: null, anchorHeight: dep.anchorHeight, anchorConfirms: dep.anchorConfirms, confirmed: false };
  const c = await confirmAnchor(dep.anchorTxid, { maxWaitMs: 0 });
  await prisma.cloudDeployment.update({
    where: { id: dep.id },
    data: { anchorHeight: c.height, anchorConfirms: c.confirms },
  });
  return { anchorTxid: dep.anchorTxid, anchorHeight: c.height, anchorConfirms: c.confirms, confirmed: c.confirmed };
}

// ---------------------------------------------------------------------------
// estimateDeployCost — what deploying costs, with live anchor readiness
// ---------------------------------------------------------------------------

export interface DeployCostEstimate {
  /** Deploying itself costs the developer 0 nANM: the anchor tx gas is paid by the platform
   *  wallet and the DA put is node-local. What the developer sees is the per-execution price. */
  developerDeployFeeNanm: bigint;
  perCallTypicalNanm: bigint;
  perCallMaxNanm: bigint;
  feeBps: number;
  policyVersion: number;
  anchor: {
    enabled: boolean;
    walletAddress: string | null;
    walletBalanceNanm: bigint | null;
    /** Whether an anchor broadcast can actually happen right now, and if not, why. */
    willBroadcast: boolean;
    reason: string | null;
  };
}

export async function estimateDeployCost(input: {
  functionId?: string;
  timeoutMs?: number;
  memoryMb?: number;
  surchargeNanm?: bigint;
  accountId?: string;
}): Promise<DeployCostEstimate> {
  let timeoutMs = input.timeoutMs ?? limits.defaultTimeoutMs;
  let memoryMb = input.memoryMb ?? limits.defaultMemoryMb;
  let surchargeNanm = input.surchargeNanm ?? 0n;
  let accountId = input.accountId ?? null;

  if (input.functionId) {
    const fn = await prisma.cloudFunction.findUnique({
      where: { id: input.functionId },
      select: { timeoutMs: true, memoryMb: true, perCallNanm: true, ownerId: true },
    });
    if (!fn) throw new ApiError(404, 'not_found', 'function not found');
    timeoutMs = fn.timeoutMs;
    memoryMb = fn.memoryMb;
    surchargeNanm = fn.perCallNanm;
    accountId = accountId ?? fn.ownerId;
  }

  const policy = await activePolicy();
  const feeBps = accountId ? await feeBpsForDeveloper(accountId, policy.platformFeeBps) : policy.platformFeeBps;
  const est = estimate({ timeoutMs, memoryMb, surchargeNanm, feeBps }, policy);

  // Live anchor readiness: the SAME pre-flight broadcastAnchor performs, so the UI can say
  // "this deploy will anchor" / "this deploy will be unanchored because X" before the fact.
  let anchor: DeployCostEstimate['anchor'];
  if (!flags.anchoring) {
    anchor = { enabled: false, walletAddress: null, walletBalanceNanm: null, willBroadcast: false, reason: 'anchoring disabled by the operator' };
  } else {
    const wallet = await anchorWallet();
    if (!wallet) {
      anchor = {
        enabled: true,
        walletAddress: null,
        walletBalanceNanm: null,
        willBroadcast: false,
        reason: `anchor wallet label "${runtime.anchorWalletLabel}" not found in the platform wallet store`,
      };
    } else {
      let balance: bigint | null = null;
      let reason: string | null = null;
      try {
        balance = (await getAddressBalance(wallet.address)).nanm;
        if (balance <= 0n) reason = 'anchor wallet has zero balance and cannot pay gas';
      } catch (e: any) {
        reason = `could not read the anchor wallet balance: ${String(e?.message ?? e).slice(0, 200)}`;
      }
      anchor = {
        enabled: true,
        walletAddress: wallet.address,
        walletBalanceNanm: balance,
        willBroadcast: balance != null && balance > 0n,
        reason,
      };
    }
  }

  return {
    developerDeployFeeNanm: 0n,
    perCallTypicalNanm: est.typicalNanm,
    perCallMaxNanm: est.maxNanm,
    feeBps,
    policyVersion: policy.version,
    anchor,
  };
}
