// ANM Data-Availability (DA) layer client — audio/video canonical storage for music.anm/video.anm.
//
// Media bytes live in the DA blob store, reached over the node JSON-RPC (config.rpcUrl, da.put/da.get).
// A single blob is capped at 32 MiB, so a file is split into ordered chunks; the ordered list of
// blob ids is the media's DA manifest (MediaItem.daBlobsJson). Retrieval concatenates the chunks
// back. The DA store is content-addressed (blob_id = sha3-256 hex), so fetched bytes are verifiable.

import { createHash } from 'crypto';
import { config } from './config';

export const DA_CHUNK_BYTES = 30 * 1024 * 1024; // under the 32 MiB node cap, with headroom

let rpcId = 1;
async function daRpc<T = any>(method: string, params: any[], timeoutMs = 60_000): Promise<T> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(config.rpcUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: rpcId++, method, params }),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new Error(`da rpc ${method} http ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(`da rpc ${method}: ${data.error.message ?? JSON.stringify(data.error)}`);
    return data.result as T;
  } finally {
    clearTimeout(t);
  }
}

function sha3(bytes: Buffer): string {
  return createHash('sha3-256').update(bytes).digest('hex');
}

// Put one blob (<=32 MiB). Returns its content-address blob id.
async function putBlob(bytes: Buffer, contentType: string, owner: string): Promise<string> {
  const r = await daRpc<any>('da.put', [{
    bytes: bytes.toString('base64'),
    metadata: { content_type: contentType, owner },
  }]);
  const id = r?.blob_id ?? r?.blobId ?? r?.id;
  if (!id) throw new Error(`da.put returned no blob id: ${JSON.stringify(r)}`);
  return String(id);
}

// Get one blob back, verifying it hashes to its id.
async function getBlob(blobId: string): Promise<Buffer> {
  const r = await daRpc<any>('da.get', [{ blob_id: blobId }]);
  const b64 = r?.bytes;
  if (typeof b64 !== 'string') throw new Error(`da.get returned no bytes for ${blobId}`);
  const buf = Buffer.from(b64, 'base64');
  if (sha3(buf) !== blobId.toLowerCase()) {
    throw new Error(`DA blob ${blobId} failed content-hash verification`);
  }
  return buf;
}

// Store a whole media file across as many <=32MiB DA blobs as needed; returns the ordered ids.
export async function daStoreMedia(bytes: Buffer, contentType: string, owner: string): Promise<{
  blobIds: string[];
  size: number;
  sha3: string;
}> {
  const blobIds: string[] = [];
  for (let off = 0; off < bytes.length; off += DA_CHUNK_BYTES) {
    const chunk = bytes.subarray(off, Math.min(off + DA_CHUNK_BYTES, bytes.length));
    blobIds.push(await putBlob(chunk, contentType, owner));
  }
  if (blobIds.length === 0) throw new Error('empty media');
  return { blobIds, size: bytes.length, sha3: sha3(bytes) };
}

// Reassemble a media file from its ordered DA blob ids.
export async function daFetchMedia(blobIds: string[]): Promise<Buffer> {
  const parts: Buffer[] = [];
  for (const id of blobIds) parts.push(await getBlob(id));
  return Buffer.concat(parts);
}

// Stream a file on disk into DA in <=32MiB chunks (never loads the whole file into memory) —
// used by the upload route after the request body is streamed to disk. Returns the ordered ids.
export async function daStoreFromFile(path: string, contentType: string, owner: string): Promise<{
  blobIds: string[];
  size: number;
  sha3: string;
}> {
  const fs = await import('fs');
  const stat = await fs.promises.stat(path);
  const size = stat.size;
  const whole = createHash('sha3-256');
  const blobIds: string[] = [];
  const fd = await fs.promises.open(path, 'r');
  try {
    const buf = Buffer.allocUnsafe(DA_CHUNK_BYTES);
    let off = 0;
    while (off < size) {
      const want = Math.min(DA_CHUNK_BYTES, size - off);
      const { bytesRead } = await fd.read(buf, 0, want, off);
      if (bytesRead <= 0) break;
      const chunk = buf.subarray(0, bytesRead);
      whole.update(chunk);
      blobIds.push(await putBlob(Buffer.from(chunk), contentType, owner));
      off += bytesRead;
    }
  } finally {
    await fd.close();
  }
  if (blobIds.length === 0) throw new Error('empty media file');
  return { blobIds, size, sha3: whole.digest('hex') };
}

export function parseBlobIds(json: string): string[] {
  try {
    const a = JSON.parse(json || '[]');
    return Array.isArray(a) ? a.map(String) : [];
  } catch {
    return [];
  }
}
