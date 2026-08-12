import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/db';
import { resolveMiner } from '@/lib/mediaQueue';
import { fileResponse } from '@/lib/mediaStore';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// A registered miner fetches a job's input file. MINER BEARER ONLY — user uploads are
// private and are never served on any public read path (the uploader already has the file).

function bearer(req: NextRequest): string {
  const h = req.headers.get('authorization') || '';
  return h.startsWith('Bearer ') ? h.slice(7).trim() : '';
}

export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  const miner = await resolveMiner(bearer(req));
  if (!miner) return NextResponse.json({ error: 'unregistered_miner' }, { status: 401 });

  const up = await prisma.mediaUpload.findUnique({ where: { id: params.id } });
  if (!up || up.expiresAt < new Date()) {
    return NextResponse.json({ error: 'not_found' }, { status: 404 });
  }
  try {
    return fileResponse(up.path, { mime: up.mime, bytes: Number(up.bytes) });
  } catch {
    return NextResponse.json({ error: 'gone' }, { status: 410 });
  }
}
