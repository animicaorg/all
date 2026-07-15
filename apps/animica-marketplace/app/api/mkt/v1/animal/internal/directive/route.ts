import { NextRequest, NextResponse } from 'next/server';
import { requireInternal } from '@/lib/animalApi';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// INTERNAL. Lets the local operator inject a steering goal from the CLI (`animica animal say ...`)
// without a browser session. Same effect as typing it into the console chat.
export async function POST(req: NextRequest) {
  const bad = requireInternal(req);
  if (bad) return bad;
  let body: any = {};
  try { body = await req.json(); } catch {}
  const text = typeof body?.text === 'string' ? body.text.trim().slice(0, 2000) : '';
  const kind = ['goal', 'instruction', 'note'].includes(body?.kind) ? body.kind : 'goal';
  if (!text) return NextResponse.json({ error: 'text required' }, { status: 400 });
  const row = await prisma.animalDirective.create({ data: { role: 'operator', kind, text } });
  return NextResponse.json({ ok: true, id: row.id });
}
