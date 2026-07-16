import { prisma } from './db';
import { chunkText, embed } from './embed';

// Per-character knowledge base for the livestream brain. Reuses the marketplace embed()
// helper; stores vectors as JSON and scores cosine in-process (small per-character corpus),
// falling back to keyword match when embeddings are unavailable.

export async function ingestKnowledge(ref: string, source: string, text: string): Promise<number> {
  const chunks = chunkText(text, 800, 100).slice(0, 400);
  if (!chunks.length) return 0;
  let vecs: number[][] | null = null;
  try { vecs = await embed(chunks); } catch { vecs = null; }
  const rows = chunks.map((content, i) => ({
    ref, source: source.slice(0, 200), content,
    embeddingJson: vecs && vecs[i] ? JSON.stringify(vecs[i]) : '',
  }));
  for (let i = 0; i < rows.length; i += 100) {
    await prisma.animalKnowledgeChunk.createMany({ data: rows.slice(i, i + 100) });
  }
  return rows.length;
}

export async function queryKnowledge(ref: string, query: string, k = 4): Promise<{ text: string }[]> {
  const chunks = await prisma.animalKnowledgeChunk.findMany({ where: { ref }, take: 2000 });
  if (!chunks.length) return [];
  let qv: number[] | null = null;
  try { qv = (await embed([query]))[0]; } catch { qv = null; }
  if (qv) {
    const scored = chunks
      .filter((c) => c.embeddingJson)
      .map((c) => { let v: number[] = []; try { v = JSON.parse(c.embeddingJson); } catch { /* */ } return { c, s: cosine(qv as number[], v) }; })
      .sort((a, b) => b.s - a.s)
      .slice(0, k);
    if (scored.length) return scored.map((x) => ({ text: x.c.content }));
  }
  const ql = query.toLowerCase().slice(0, 40);
  return chunks.filter((c) => c.content.toLowerCase().includes(ql)).slice(0, k).map((c) => ({ text: c.content }));
}

export async function clearKnowledge(ref: string): Promise<number> {
  const r = await prisma.animalKnowledgeChunk.deleteMany({ where: { ref } });
  return r.count;
}

function cosine(a: number[], b: number[]): number {
  const n = Math.min(a.length, b.length);
  let d = 0, na = 0, nb = 0;
  for (let i = 0; i < n; i++) { d += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
  return d / (Math.sqrt(na) * Math.sqrt(nb) + 1e-9);
}
