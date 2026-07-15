#!/usr/bin/env node
/**
 * Publish the official native .anm sites.
 *
 * A "native" .anm site is self-contained HTML stored in the content-addressed layer
 * (CID = anm1c<sha3_256(bytes)>) with the domain's contentCid pointing at it — so the
 * gateway serves the bytes itself, hash-verified, in an opaque-origin sandbox, instead
 * of 302-redirecting to some other web property.
 *
 * This is the operator-side path for the names the treasury already owns; ordinary users
 * publish through the owner-authenticated API (POST /api/mkt/v1/names/<name>/publish).
 *
 * Usage:  node scripts/publish_anm_sites.mjs [name ...]     (default: every file in anm-sites/)
 */
import { PrismaClient } from '@prisma/client';
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SITES = join(HERE, '..', 'anm-sites');
const prisma = new PrismaClient();

const CID_PREFIX = 'anm1c';
const MAX_BYTES = 2 * 1024 * 1024;
const cidOf = (b) => CID_PREFIX + createHash('sha3-256').update(b).digest('hex');

// Records for names we create/repair here. `endpoint` stays as a graceful fallback: the
// gateway prefers contentCid, and falls back to the endpoint if content is ever missing.
const META = {
  animica: { kind: 'app', avatar: '🌐', description: 'The Animica Internet — search every .anm site.', endpoint: 'https://animica.dev/portal' },
  market:  { kind: 'app', avatar: '🛒', description: 'ANM-native AI marketplace — agents, RAG, media.', endpoint: 'https://animica.dev/marketplace' },
  docs:    { kind: 'app', avatar: '📚', description: 'Animica developer docs & Animica Internet tutorials.', endpoint: 'https://animica.dev/docs' },
  media:   { kind: 'ai',  avatar: '🎬', description: 'Generative media served by miners — image, video, audio.', endpoint: 'https://animica.dev/marketplace?type=MEDIA' },
  agents:  { kind: 'agent', avatar: '🤖', description: 'Autonomous agents with their own .anm identities.', endpoint: 'https://animica.dev/names?kind=agent' },
  search:  { kind: 'app', avatar: '🔎', description: 'Search the sovereign .anm namespace.', endpoint: 'https://animica.dev/names' },
  studio:  { kind: 'app', avatar: '🎛️', description: 'Animica Studio — build with Animica AI.', endpoint: 'https://animica.dev/studio' },
  wallet:  { kind: 'app', avatar: '👛', description: 'Non-custodial post-quantum ANM wallet.', endpoint: 'https://wallet.animica.org' },
  forge:   { kind: 'app', avatar: '🔨', description: 'Animica Forge — prompt to app.', endpoint: 'https://animica.io' },
  vpn:     { kind: 'app', avatar: '🛡️', description: 'Animica dVPN — pick an exit, relays earn ANM.', endpoint: 'https://animica.dev/vpn' },
};

async function owner() {
  const acct = await prisma.account.findFirst({ where: { address: { startsWith: 'anim1marketplace-treasury' } } });
  if (!acct) throw new Error('treasury account not found — cannot publish official names');
  return acct;
}

async function publishOne(name, html, ownerId) {
  const bytes = Buffer.from(html, 'utf8');
  if (bytes.length > MAX_BYTES) throw new Error(`${name}: ${bytes.length}B exceeds the ${MAX_BYTES}B object cap`);
  const cid = cidOf(bytes);

  const existingObj = await prisma.contentObject.findUnique({ where: { cid }, select: { cid: true } });
  if (!existingObj) {
    await prisma.contentObject.create({
      data: { cid, mime: 'text/html; charset=utf-8', bytes, size: bytes.length, publishedBy: ownerId },
    });
  }

  const meta = META[name] ?? { kind: 'app', avatar: '🌐', description: `${name}.anm on the Animica Internet.` };
  const domain = await prisma.anmDomain.findUnique({ where: { name } });
  const records = JSON.stringify({ ...meta, official: true });

  let d;
  if (domain) {
    // Preserve the existing owner; only repair records + point at the new content.
    const prev = (() => { try { return JSON.parse(domain.recordsJson) || {}; } catch { return {}; } })();
    d = await prisma.anmDomain.update({
      where: { name },
      data: { contentCid: cid, recordsJson: JSON.stringify({ ...prev, ...meta, official: true }) },
    });
  } else {
    d = await prisma.anmDomain.create({
      data: {
        name, ownerId, kind: meta.kind, recordsJson: records, contentCid: cid,
        expiresAt: new Date(Date.now() + 10 * 365 * 86400_000), status: 'ACTIVE',
      },
    });
  }
  await prisma.domainEvent.create({
    data: { domainId: d.id, kind: 'update', detail: `publish ${cid.slice(0, 14)}… (${bytes.length}B) native` },
  }).catch(() => {});
  return { cid, size: bytes.length, created: !domain, deduped: !!existingObj };
}

async function main() {
  const only = process.argv.slice(2);
  if (!existsSync(SITES)) throw new Error(`no anm-sites/ directory at ${SITES}`);
  const files = readdirSync(SITES).filter((f) => f.endsWith('.html'));
  const targets = only.length ? files.filter((f) => only.includes(f.replace(/\.html$/, ''))) : files;
  if (!targets.length) throw new Error(`nothing to publish (looked for: ${only.join(', ') || '*.html'})`);

  const acct = await owner();
  console.log(`publishing ${targets.length} native .anm site(s) as ${acct.address}\n`);
  for (const f of targets.sort()) {
    const name = f.replace(/\.html$/, '');
    const html = readFileSync(join(SITES, f), 'utf8');
    try {
      const r = await publishOne(name, html, acct.id);
      console.log(
        `  ${r.created ? 'CREATE' : 'UPDATE'}  ${name}.anm  ${r.cid.slice(0, 18)}…  ${String(r.size).padStart(6)}B` +
        `${r.deduped ? '  (object deduped)' : ''}`,
      );
    } catch (e) {
      console.error(`  FAIL    ${name}.anm  ${e.message}`);
      process.exitCode = 1;
    }
  }
}

main()
  .catch((e) => { console.error('fatal:', e.message); process.exitCode = 1; })
  .finally(() => prisma.$disconnect());
