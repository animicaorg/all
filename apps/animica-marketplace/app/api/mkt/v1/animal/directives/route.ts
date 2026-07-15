import { NextRequest, NextResponse } from 'next/server';
import { requireConsole } from '@/lib/animalApi';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// The steering chat. GET returns the recent conversation (operator goals + agent replies).
// POST appends an operator directive that the engine reads on its next cycle.
export async function GET() {
  const unauth = await requireConsole();
  if (unauth) return unauth;
  const rows = await prisma.animalDirective.findMany({ orderBy: { createdAt: 'desc' }, take: 60 });
  return NextResponse.json({ directives: rows.reverse() });
}

export async function POST(req: NextRequest) {
  const unauth = await requireConsole();
  if (unauth) return unauth;
  let body: any = {};
  try { body = await req.json(); } catch {}
  const text = typeof body?.text === 'string' ? body.text.trim().slice(0, 2000) : '';
  const kind = ['goal', 'instruction', 'note'].includes(body?.kind) ? body.kind : 'goal';
  if (!text) return NextResponse.json({ error: 'text required' }, { status: 400 });
  const row = await prisma.animalDirective.create({ data: { role: 'operator', kind, text } });
  return NextResponse.json({ ok: true, directive: row });
}
