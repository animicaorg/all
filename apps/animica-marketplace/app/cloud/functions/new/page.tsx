import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';
import { limits, runtime, PLAN_ENTITLEMENTS } from '@/lib/cloud/config';
import { resolvePlan } from '@/lib/cloud/entitlements';
import { cloudSession, cloudAccount, ownerSegment } from '@/components/cloud/server';
import CloudGate from '@/components/cloud/CloudGate';
import NewFunctionClient, { type EditorBootstrap } from './NewFunctionClient';

export const dynamic = 'force-dynamic';

// A real, working starter — not a placeholder: it runs as-is and demonstrates the ABI
// (main(request, ctx), animica.log, ctx.request_id).
const STARTER_SOURCE = `import animica

def main(request, ctx):
    """Handle one HTTPS call.

    request: the caller's JSON body (dict)
    ctx:     execution context (ctx.request_id, ctx.caller, ...)
    Return any JSON-serializable value — it becomes the HTTP response.
    """
    name = str(request.get("name", "world"))
    animica.log(f"greeting {name}")
    return {"greeting": f"Hello, {name}!", "request_id": ctx.request_id}
`;

// /cloud/functions/new — the editor. ?from=<functionId>[&version=<n>] pre-loads an existing
// function's snapshot so "edit & redeploy" and "open old version in editor" share this page.
export default async function NewFunctionPage({
  searchParams,
}: {
  searchParams?: { from?: string; version?: string };
}) {
  const sess = cloudSession();
  if (!sess) return <CloudGate />;
  const me = sess.accountId;

  const [account, plan, fnCount] = await Promise.all([
    cloudAccount(me),
    resolvePlan(me),
    prisma.cloudFunction.count({ where: { ownerId: me } }),
  ]);
  if (!account) return <CloudGate />;

  let prefill: EditorBootstrap['prefill'] = {
    mode: 'create',
    functionId: null,
    name: '',
    slug: '',
    description: '',
    entrypoint: 'main',
    source: STARTER_SOURCE,
    timeoutMs: limits.defaultTimeoutMs,
    memoryMb: limits.defaultMemoryMb,
    capabilities: [],
    visibility: 'PUBLIC',
    requiresAuth: false,
    perCallNanm: '0',
    loadedVersion: null,
  };

  if (searchParams?.from) {
    const fn = await prisma.cloudFunction.findFirst({
      where: { id: searchParams.from, ownerId: me },
      include: { versions: { orderBy: { version: 'desc' }, take: 1 } },
    });
    if (fn) {
      const wantVersion = Number(searchParams.version);
      const versionRow = Number.isInteger(wantVersion) && wantVersion > 0
        ? await prisma.cloudFunctionVersion.findUnique({
            where: { functionId_version: { functionId: fn.id, version: wantVersion } },
          })
        : (await prisma.cloudFunctionVersion.findUnique({
            where: { functionId_version: { functionId: fn.id, version: fn.currentVersion } },
          })) ?? fn.versions[0] ?? null;
      prefill = {
        mode: 'redeploy',
        functionId: fn.id,
        name: fn.name,
        slug: fn.slug,
        description: fn.description,
        entrypoint: versionRow?.entrypoint ?? fn.entrypoint,
        source: versionRow?.source ?? STARTER_SOURCE,
        timeoutMs: fn.timeoutMs,
        memoryMb: fn.memoryMb,
        capabilities: fn.capabilities,
        visibility: fn.visibility,
        requiresAuth: fn.requiresAuth,
        perCallNanm: fn.perCallNanm.toString(),
        loadedVersion: versionRow?.version ?? null,
      };
    }
  }

  const boot: EditorBootstrap = {
    ownerSegment: ownerSegment(account),
    publicBase: runtime.publicBase,
    plan: {
      key: plan.key,
      maxFunctions: plan.limits.max_functions,
      functionsUsed: fnCount,
      privateDeployments: plan.limits.private_deployments,
      marketplacePublishing: plan.limits.marketplace_publishing,
      feeBps: plan.founding && plan.founding.feeBps >= 0 ? plan.founding.feeBps : null,
    },
    limits: {
      maxSourceBytes: limits.maxSourceBytes,
      minTimeoutMs: limits.minTimeoutMs,
      maxTimeoutMs: limits.maxTimeoutMs,
      minMemoryMb: limits.minMemoryMb,
      maxMemoryMb: limits.maxMemoryMb,
    },
    anchorConfirmations: runtime.anchorConfirmations,
    // The lowest plan whose defaults allow another function — drives the honest upsell copy.
    upgradeHint:
      plan.limits.max_functions !== -1 && fnCount >= plan.limits.max_functions
        ? {
            needed: fnCount + 1,
            proAllows: PLAN_ENTITLEMENTS.pro.max_functions,
            developerAllows: PLAN_ENTITLEMENTS.developer.max_functions,
          }
        : null,
    prefill,
  };

  return <NewFunctionClient boot={jsonSafe(boot)} />;
}
