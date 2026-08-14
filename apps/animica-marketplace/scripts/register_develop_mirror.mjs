// Register develop.anm as an ENDPOINT record that mirrors animica.dev.
// The gateway (app/anm/[...seg]/route.ts) 302-redirects a name with recordsJson.endpoint (and
// no contentCid) to that endpoint, preserving subpath+query — so develop.anm and develop.anm/x
// resolve to https://animica.dev and https://animica.dev/x. Owned by the marketplace treasury
// account (like the other official names), zero-fee (direct DB, operator action).
import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();
const NAME = 'develop';
const ENDPOINT = 'https://animica.dev';

async function main() {
  const owner = await prisma.account.findFirst({
    where: { address: { startsWith: 'anim1marketplace-treasury' } },
  });
  if (!owner) throw new Error('treasury account not found — run an official-names publish first');

  const records = JSON.stringify({
    kind: 'app', avatar: '🛠️',
    description: 'Animica.dev — build a site from chat, mirrored onto the Animica Internet.',
    endpoint: ENDPOINT, official: true, mirror: 'animica.dev',
  });
  const existing = await prisma.anmDomain.findUnique({ where: { name: NAME } });
  const d = existing
    ? await prisma.anmDomain.update({
        where: { name: NAME },
        // endpoint mirror: clear any native content so the gateway redirects to animica.dev.
        data: { recordsJson: records, contentCid: null, status: 'ACTIVE',
                expiresAt: new Date(Date.now() + 10 * 365 * 86400_000) },
      })
    : await prisma.anmDomain.create({
        data: { name: NAME, ownerId: owner.id, kind: 'app', recordsJson: records,
                expiresAt: new Date(Date.now() + 10 * 365 * 86400_000), status: 'ACTIVE' },
      });
  await prisma.domainEvent.create({
    data: { domainId: d.id, kind: existing ? 'update' : 'register', detail: `mirror -> ${ENDPOINT}` },
  }).catch(() => {});
  console.log(`${existing ? 'updated' : 'registered'} ${NAME}.anm -> ${ENDPOINT} (owner ${owner.address})`);
}

main().catch((e) => { console.error(e); process.exit(1); }).finally(() => prisma.$disconnect());
