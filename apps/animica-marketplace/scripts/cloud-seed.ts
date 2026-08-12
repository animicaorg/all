// Seed the Animica Python Cloud economic configuration.
//
//   npx tsx scripts/cloud-seed.ts [--dry-run]
//
// Creates the ACTIVE PricingPolicy row (version 1) from the env-configured bootstrap defaults in
// lib/cloud/config.ts. Idempotent: re-running never creates a second active policy and never
// rewrites an existing one (historical accounting must stay reproducible — §88, §92). To change
// pricing later, use the admin pricing console, which writes a NEW version and flips `active`.

import { prisma } from '../lib/db';
import { economics } from '../lib/cloud/config';

const dryRun = process.argv.includes('--dry-run');

async function main() {
  const existing = await prisma.pricingPolicy.findFirst({ where: { active: true }, orderBy: { version: 'desc' } });
  if (existing) {
    console.log(
      JSON.stringify({
        at: new Date().toISOString(),
        msg: 'active pricing policy already present — leaving it alone',
        version: existing.version,
        platformFeeBps: existing.platformFeeBps,
        targetMarginBps: existing.targetMarginBps,
      }),
    );
    return;
  }

  const data = {
    version: 1,
    active: true,
    note: 'bootstrap policy seeded from lib/cloud/config.ts env defaults',
    baseCallNanm: economics.baseCallNanm,
    cpuMsNanm: economics.cpuMsNanm,
    memMbMsNanm: economics.memMbMsNanm,
    aiTokenInNanm: economics.aiTokenInNanm,
    aiTokenOutNanm: economics.aiTokenOutNanm,
    egressKbNanm: economics.egressKbNanm,
    gpuMsNanm: economics.gpuMsNanm,
    platformFeeBps: economics.platformFeeBps,
    providerShareBps: economics.providerShareBps,
    costCpuMsNanm: economics.costCpuMsNanm,
    costMemMbMsNanm: economics.costMemMbMsNanm,
    costAiTokenNanm: economics.costAiTokenNanm,
    costEgressKbNanm: economics.costEgressKbNanm,
    costPerCallNanm: economics.costPerCallNanm,
    targetMarginBps: economics.targetMarginBps,
    enforceMinMargin: economics.enforceMinMargin,
    freeExecutionsPerDay: economics.freeExecutionsPerDay,
    freeExecutionsPerMonth: economics.freeExecutionsPerMonth,
    freeAiTokensPerDay: economics.freeAiTokensPerDay,
    freeTierMonthlyCeilingNanm: economics.freeTierMonthlyCeilingNanm,
    anmUsdFloorMicros: economics.anmUsdFloorMicros,
    createdBy: 'cloud-seed',
  };

  if (dryRun) {
    console.log(JSON.stringify({ at: new Date().toISOString(), msg: 'DRY RUN — would create', data }, (_k, v) => (typeof v === 'bigint' ? v.toString() : v)));
    return;
  }

  const created = await prisma.pricingPolicy.create({ data });
  console.log(
    JSON.stringify({
      at: new Date().toISOString(),
      msg: 'seeded active pricing policy',
      version: created.version,
      platformFeeBps: created.platformFeeBps,
      providerShareBps: created.providerShareBps,
      targetMarginBps: created.targetMarginBps,
    }),
  );
}

main()
  .catch((e) => {
    console.error(JSON.stringify({ at: new Date().toISOString(), level: 'error', msg: String(e?.message ?? e) }));
    process.exitCode = 1;
  })
  .finally(() => prisma.$disconnect());
