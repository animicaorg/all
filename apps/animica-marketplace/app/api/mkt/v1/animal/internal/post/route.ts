import { NextRequest, NextResponse } from 'next/server';
import { requireInternal } from '@/lib/animalApi';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const ALLOWED = new Set(['DRAFT', 'QUEUED', 'DRY_RUN', 'POSTED', 'FAILED', 'SKIPPED']);

// INTERNAL. The engine records each unit of content it produced (a dry-run preview or a live post),
// and may drop a short agent reply into the steering chat. Updates the channel's lastPostAt.
export async function POST(req: NextRequest) {
  const bad = requireInternal(req);
  if (bad) return bad;
  let body: any = {};
  try { body = await req.json(); } catch {}

  const platform = String(body?.platform || '').slice(0, 40);
  const status = ALLOWED.has(body?.status) ? body.status : 'DRY_RUN';
  if (!platform) return NextResponse.json({ error: 'platform required' }, { status: 400 });

  const post = await prisma.animalPost.create({
    data: {
      platform,
      kind: String(body?.kind || 'text').slice(0, 20),
      status,
      caption: String(body?.caption || '').slice(0, 4000),
      mediaJobId: body?.mediaJobId ? String(body.mediaJobId).slice(0, 80) : null,
      mediaKind: body?.mediaKind ? String(body.mediaKind).slice(0, 20) : null,
      externalId: body?.externalId ? String(body.externalId).slice(0, 120) : null,
      externalUrl: body?.externalUrl ? String(body.externalUrl).slice(0, 400) : null,
      error: body?.error ? String(body.error).slice(0, 400) : null,
      metaJson: JSON.stringify(body?.meta ?? {}).slice(0, 8000),
      postedAt: status === 'POSTED' ? new Date() : null,
    },
  });

  if (status === 'POSTED') {
    await prisma.socialConnection.update({ where: { platform }, data: { lastPostAt: new Date() } }).catch(() => {});
  }
  if (typeof body?.reply === 'string' && body.reply.trim()) {
    await prisma.animalDirective.create({
      data: { role: 'agent', kind: 'reply', text: String(body.reply).trim().slice(0, 2000) },
    });
  }
  return NextResponse.json({ ok: true, id: post.id });
}
