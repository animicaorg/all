import { prisma } from './db';
import { config } from './config';

// RAG embeddings + retrieval over pgvector. The free /v1 has NO embeddings; the pool `anm-embed`
// worker returns 768-dim (nomic-embed-text). We VALIDATE length on every response (a misconfigured
// worker can return a different dimension and poison the fixed-dim column). Embeddings are computed
// at publish time with retries — NEVER synchronously in a user request (worker jobs can hang 120s).

const EMBED_DIM = 768;

export async function embed(texts: string[]): Promise<number[][]> {
  if (!config.poolKey) throw new Error('ANIMICA_POOL_KEY required for embeddings');
  const res = await fetch(`${config.poolV1}/embeddings`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${config.poolKey}` },
    body: JSON.stringify({ model: config.embedModel, input: texts }),
  });
  if (!res.ok) throw new Error(`embeddings ${res.status}: ${(await res.text()).slice(0, 200)}`);
  const data = await res.json();
  const vecs: number[][] = (data.data ?? []).map((d: any) => d.embedding);
  for (const v of vecs) {
    if (!Array.isArray(v) || v.length !== EMBED_DIM) {
      throw new Error(`bad embedding dim ${v?.length} (expected ${EMBED_DIM})`);
    }
  }
  return vecs;
}

function toVectorLiteral(v: number[]): string {
  return `[${v.join(',')}]`;
}

// Store an embedding on a chunk (raw SQL — Prisma has no vector type).
export async function storeChunkEmbedding(chunkId: string, vec: number[]): Promise<void> {
  const lit = toVectorLiteral(vec);
  await prisma.$executeRawUnsafe(
    `UPDATE "Chunk" SET embedding = $1::vector, embedded = true WHERE id = $2`,
    lit,
    chunkId,
  );
}

export interface RetrievedChunk { id: string; content: string; distance: number }

// Cosine-nearest chunks for a listing. Falls back to keyword ILIKE if embeddings unavailable.
export async function retrieve(listingId: string, queryVec: number[] | null, query: string, k = 6): Promise<RetrievedChunk[]> {
  if (queryVec && queryVec.length === EMBED_DIM) {
    const lit = toVectorLiteral(queryVec);
    const rows = await prisma.$queryRawUnsafe<any[]>(
      `SELECT id, content, (embedding <=> $1::vector) AS distance
         FROM "Chunk"
        WHERE "listingId" = $2 AND embedded = true
        ORDER BY embedding <=> $1::vector
        LIMIT $3`,
      lit,
      listingId,
      k,
    );
    return rows.map((r) => ({ id: r.id, content: r.content, distance: Number(r.distance) }));
  }
  // Keyword fallback.
  const rows = await prisma.chunk.findMany({
    where: { listingId, content: { contains: query.slice(0, 60), mode: 'insensitive' } },
    take: k,
    select: { id: true, content: true },
  });
  return rows.map((r) => ({ id: r.id, content: r.content, distance: 1 }));
}

// Cheap dependency-free chunker (mirrors python/animica/ai/rag.py: ~800 chars / 100 overlap).
export function chunkText(text: string, size = 800, overlap = 100): string[] {
  const clean = text.replace(/\s+/g, ' ').trim();
  if (!clean) return [];
  const out: string[] = [];
  let i = 0;
  while (i < clean.length) {
    out.push(clean.slice(i, i + size));
    i += size - overlap;
  }
  return out;
}
