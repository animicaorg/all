import { NextRequest, NextResponse } from 'next/server';
import { requireInternal } from '@/lib/animalApi';
import { queryKnowledge } from '@/lib/animalKnowledge';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// INTERNAL (Bearer + edge-denied): the livestream brain queries a character's knowledge
// base here to ground its replies (RAG). Returns the top matching chunks.
export async function POST(req: NextRequest) {
  const bad = requireInternal(req);
  if (bad) return bad;
  let b: any = {};
  try { b = await req.json(); } catch { /* ignore */ }
  const ref = String(b?.ref || '');
  const query = String(b?.query || '');
  const k = Math.min(8, Math.max(1, Number(b?.k) || 4));
  if (!ref || !query) return NextResponse.json({ chunks: [] });
  try {
    return NextResponse.json({ chunks: await queryKnowledge(ref, query, k) });
  } catch (e: any) {
    return NextResponse.json({ chunks: [], error: String(e?.message || e).slice(0, 200) });
  }
}
