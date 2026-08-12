// Register music.anm and video.anm as ENDPOINT records pointing at the hosted apps
// (animica.dev/music and animica.dev/video), owned by the marketplace treasury account.
// The gateway (app/anm/[...seg]/route.ts) 302-redirects them, preserving sub-path+query.
import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();
const NAMES = [
  { name: 'music', endpoint: 'https://animica.dev/music', avatar: '🎵',
    description: 'Animica Music — stream tracks stored on the ANM data-availability layer, tip artists in ANM.' },
  { name: 'video', endpoint: 'https://animica.dev/video', avatar: '🎬',
    description: 'Animica Video — watch videos stored on the ANM data-availability layer, tip creators in ANM.' },
];

async function main() {
  const owner = await prisma.account.findFirst({ where: { address: { startsWith: 'anim1marketplace-treasury' } } });
  if (!owner) throw new Error('treasury account not found');
  for (const n of NAMES) {
    const records = JSON.stringify({ kind: 'app', avatar: n.avatar, description: n.description,
      endpoint: n.endpoint, official: true });
    const existing = await prisma.anmDomain.findUnique({ where: { name: n.name } });
    const d = existing
      ? await prisma.anmDomain.update({ where: { name: n.name },
          data: { recordsJson: records, contentCid: null, status: 'ACTIVE',
                  expiresAt: new Date(Date.now() + 10 * 365 * 86400_000) } })
      : await prisma.anmDomain.create({ data: { name: n.name, ownerId: owner.id, kind: 'app',
          recordsJson: records, expiresAt: new Date(Date.now() + 10 * 365 * 86400_000), status: 'ACTIVE' } });
    await prisma.domainEvent.create({ data: { domainId: d.id, kind: existing ? 'update' : 'register',
      detail: `endpoint -> ${n.endpoint}` } }).catch(() => {});
    console.log(`${existing ? 'updated' : 'registered'} ${n.name}.anm -> ${n.endpoint}`);
  }
}
main().catch((e) => { console.error(e); process.exit(1); }).finally(() => prisma.$disconnect());
