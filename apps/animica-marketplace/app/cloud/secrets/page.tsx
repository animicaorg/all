import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';
import { limits } from '@/lib/cloud/config';
import { resolvePlan } from '@/lib/cloud/entitlements';
import { cloudSession } from '@/components/cloud/server';
import CloudGate from '@/components/cloud/CloudGate';
import SecretsClient, { type SecretsDto } from './SecretsClient';

export const dynamic = 'force-dynamic';

// /cloud/secrets — encrypted values injected into authorized executions. The value is sealed
// (AES-256-GCM via lib/vault) at creation and NEVER returned to any client again; the list
// below carries only names + a 4-char hint.
export default async function CloudSecretsPage() {
  const sess = cloudSession();
  if (!sess) return <CloudGate />;
  const me = sess.accountId;

  const [plan, secrets, functions] = await Promise.all([
    resolvePlan(me),
    prisma.cloudSecret.findMany({
      where: { ownerId: me },
      orderBy: { createdAt: 'desc' },
      select: {
        id: true, name: true, hint: true, functionId: true, lastUsedAt: true, createdAt: true,
        function: { select: { slug: true } },
      },
    }),
    prisma.cloudFunction.findMany({
      where: { ownerId: me },
      orderBy: { updatedAt: 'desc' },
      select: { id: true, slug: true },
    }),
  ]);

  const dto: SecretsDto = jsonSafe({
    plan: { key: plan.key, maxSecrets: plan.limits.max_secrets, used: secrets.length },
    maxSecretBytes: limits.maxSecretBytes,
    secrets,
    functions,
  }) as unknown as SecretsDto;

  return <SecretsClient dto={dto} />;
}
