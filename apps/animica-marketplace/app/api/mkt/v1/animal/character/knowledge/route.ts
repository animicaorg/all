import { NextRequest, NextResponse } from 'next/server';
import { randomUUID } from 'crypto';
import { requireConsole } from '@/lib/animalApi';
import { getCharacter, saveCharacter } from '@/lib/animalCharacter';
import { ingestKnowledge, clearKnowledge } from '@/lib/animalKnowledge';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const MAX_DOC = 4 * 1024 * 1024; // 4 MiB of text per upload

// Give the character a private knowledge base (RAG). POST a document — multipart 'file'
// (.txt/.md/.csv/.json) or JSON { source, text } — and it is chunked, embedded, and stored
// under the character's knowledge_ref so the livestream brain can ground its replies in it.
// GET returns how much is loaded; DELETE clears it.
export async function GET() {
  const unauth = await requireConsole();
  if (unauth) return unauth;
  const char = await getCharacter();
  const ref = char.knowledge_ref;
  if (!ref) return NextResponse.json({ ref: '', sources: [], chunks: 0 });
  const rows = await prisma.animalKnowledgeChunk.findMany({ where: { ref }, select: { source: true } });
  const sources = Array.from(new Set(rows.map((r) => r.source)));
  return NextResponse.json({ ref, sources, chunks: rows.length });
}

export async function POST(req: NextRequest) {
  const unauth = await requireConsole();
  if (unauth) return unauth;

  let source = 'upload';
  let text = '';
  const ctype = req.headers.get('content-type') || '';
  try {
    if (ctype.includes('multipart/form-data')) {
      const form = await req.formData();
      const f = form.get('file');
      if (f && typeof f !== 'string') {
        source = (f as any).name || form.get('source')?.toString() || 'upload';
        text = await (f as Blob).text();
      } else if (typeof form.get('text') === 'string') {
        source = form.get('source')?.toString() || 'paste';
        text = String(form.get('text'));
      }
    } else {
      const b: any = await req.json();
      source = String(b?.source || 'paste').slice(0, 200);
      text = String(b?.text || '');
    }
  } catch {
    return NextResponse.json({ error: 'could not read document' }, { status: 400 });
  }

  text = text.replace(/\x00/g, '').trim();
  if (!text) return NextResponse.json({ error: 'no text content found in upload' }, { status: 400 });
  if (text.length > MAX_DOC) return NextResponse.json({ error: `document too large (max ${MAX_DOC} chars)` }, { status: 413 });

  // Ensure the character has a stable knowledge ref, minting one on first upload.
  const char = await getCharacter();
  let ref = char.knowledge_ref;
  if (!ref) { ref = `kb_${randomUUID().replace(/-/g, '')}`; await saveCharacter({ ...char, knowledge_ref: ref }); }

  const added = await ingestKnowledge(ref, source, text);
  return NextResponse.json({ ok: true, ref, source, chunksAdded: added });
}

export async function DELETE() {
  const unauth = await requireConsole();
  if (unauth) return unauth;
  const char = await getCharacter();
  if (!char.knowledge_ref) return NextResponse.json({ ok: true, cleared: 0 });
  const cleared = await clearKnowledge(char.knowledge_ref);
  return NextResponse.json({ ok: true, cleared });
}
