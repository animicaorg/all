-- pgvector column + ANN index for Chunk embeddings.
-- Prisma has no native vector type, so we manage this out of band (idempotent).
-- Embedding space: worker anm-embed => nomic-embed-text => 768 dims (validated per response).
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE "Chunk" ADD COLUMN IF NOT EXISTS embedding vector(768);

-- Cosine distance index. lists tuned small; dataset grows lazily.
CREATE INDEX IF NOT EXISTS chunk_embedding_idx
  ON "Chunk" USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
