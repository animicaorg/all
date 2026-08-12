import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import { mkdir, rename, stat, unlink } from 'node:fs/promises';
import { join, resolve } from 'node:path';

// 9.0.0 GPU Studios — gateway DISK store for large media inputs (uploads) and outputs
// (artifacts). The classic kinds keep their inline-base64 path; studio files (videos,
// songs, .blend scenes, frame zips) never enter a JSON body or a DB row. Everything is
// streamed: request body → temp file (hashing as it goes) → atomic rename into place.
// The DB (MediaUpload / MediaJob.resultPath) is the source of truth for lifecycle; the
// sweep unlinks files when their row is wiped. Nothing here trusts client sizes: the
// caller passes maxBytes and the stream is aborted the moment it is exceeded.
//
// App store (10.x) — 'builds' is the DURABLE third subdir: published APK releases
// (AppBuild.path). Builds are sweep-EXEMPT: no TTL, never wiped by mediaQueue.sweep()
// or any lazy maintenance here — their lifecycle is owned by the AppBuild row
// (lib/appStore.ts deletes the file only when the build row itself is deleted).
// Only aborted temp files (.part-* / incoming-*) are ever cleaned out of builds/.

const STORE_DIR = process.env.MEDIA_STORE_DIR || join(process.cwd(), 'var', 'media-store');

export function storeDir(sub: 'uploads' | 'artifacts' | 'builds'): string {
  return join(STORE_DIR, sub);
}

export function uploadPath(id: string): string {
  return join(storeDir('uploads'), id);
}

export function artifactPath(jobId: string): string {
  return join(storeDir('artifacts'), jobId);
}

// Durable APK store path for an AppBuild row (sweep-exempt — see header note).
export function buildPath(buildId: string): string {
  return join(storeDir('builds'), buildId);
}

// Refuse ids that could escape the store dir (ids are cuids, but never trust input).
export function safeStorePath(p: string): string {
  const abs = resolve(p);
  if (!abs.startsWith(resolve(STORE_DIR))) throw new Error('path outside media store');
  return abs;
}

export class StreamTooLargeError extends Error {
  constructor(max: number) { super(`stream exceeds ${max} bytes`); }
}

// Stream a web ReadableStream (Next.js request.body) to destPath, enforcing maxBytes and
// hashing while writing. Returns { bytes, sha3 }. On ANY failure the partial file is
// removed — a truncated upload must never become a servable input.
export async function streamToFile(
  body: ReadableStream<Uint8Array> | null,
  destPath: string,
  maxBytes: number,
): Promise<{ bytes: number; sha3: string }> {
  if (!body) throw new Error('empty body');
  await mkdir(resolve(destPath, '..'), { recursive: true });
  const tmp = `${destPath}.part-${process.pid}-${Date.now()}`;
  const hash = createHash('sha3-256');
  let bytes = 0;
  const out = createWriteStream(tmp, { flags: 'wx' });
  const reader = body.getReader();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value || value.length === 0) continue;
      bytes += value.length;
      if (bytes > maxBytes) throw new StreamTooLargeError(maxBytes);
      hash.update(value);
      if (!out.write(Buffer.from(value))) {
        await new Promise<void>((res, rej) => { out.once('drain', res); out.once('error', rej); });
      }
    }
    await new Promise<void>((res, rej) => out.end((err: unknown) => (err ? rej(err) : res())));
    if (bytes === 0) throw new Error('empty body');
    await rename(tmp, destPath);
    return { bytes, sha3: hash.digest('hex') };
  } catch (e) {
    try { out.destroy(); } catch { /* ignore */ }
    await unlink(tmp).catch(() => {});
    throw e;
  } finally {
    reader.releaseLock?.();
  }
}

export async function fileSize(path: string): Promise<number | null> {
  try { return (await stat(safeStorePath(path))).size; } catch { return null; }
}

// Free bytes on the filesystem backing the store — the gateway must never let anonymous
// uploads (or miner artifacts) fill the root disk that runs the node + services.
export async function freeDiskBytes(): Promise<number> {
  const { statfs } = await import('node:fs/promises');
  try {
    const s: any = await (statfs as any)(STORE_DIR).catch(async () => {
      await mkdir(STORE_DIR, { recursive: true });
      return (statfs as any)(STORE_DIR);
    });
    return Number(s.bavail) * Number(s.bsize);
  } catch {
    return Number.MAX_SAFE_INTEGER; // statfs unsupported — don't brick uploads over it
  }
}

// Remove abandoned .part temp files (aborted uploads/results) older than maxAgeMs.
// builds/ is included ONLY for its temp names (.part-* mid-stream files and incoming-*
// staged uploads that never reached registerBuild) — completed builds live under their
// AppBuild id, never match either pattern, and are NEVER touched here (sweep-exempt).
export async function sweepPartFiles(maxAgeMs = 3600_000): Promise<number> {
  const { readdir } = await import('node:fs/promises');
  let removed = 0;
  for (const sub of ['uploads', 'artifacts', 'builds'] as const) {
    const dir = storeDir(sub);
    let names: string[] = [];
    try { names = await readdir(dir); } catch { continue; }
    for (const n of names) {
      if (!(n.includes('.part-') || (sub === 'builds' && n.startsWith('incoming-')))) continue;
      const p = join(dir, n);
      try {
        const st = await stat(p);
        if (Date.now() - st.mtimeMs > maxAgeMs) { await unlink(p); removed++; }
      } catch { /* raced */ }
    }
  }
  return removed;
}

export async function removeFile(path: string | null | undefined): Promise<void> {
  if (!path) return;
  try { await unlink(safeStorePath(path)); } catch { /* already gone / never trust */ }
}

// Stream a stored file as a web Response (artifact download / miner input fetch).
// Pass `range` (inclusive byte offsets, pre-validated against `bytes`) for a 206 partial
// response — the store download route uses this for resumable APK downloads.
export function fileResponse(
  path: string,
  opts: { mime: string; bytes?: number | null; filename?: string; range?: { start: number; end: number } | null },
): Response {
  const abs = safeStorePath(path);
  const range = opts.range ?? null;
  if (range && opts.bytes == null) throw new Error('fileResponse: range requires total bytes');
  const nodeStream = createReadStream(abs, range ? { start: range.start, end: range.end } : undefined);
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      nodeStream.on('data', (chunk: string | Buffer) => {
        controller.enqueue(new Uint8Array(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)));
        // Backpressure: pause when the consumer's queue is full.
        if ((controller.desiredSize ?? 1) <= 0) nodeStream.pause();
      });
      nodeStream.on('end', () => controller.close());
      nodeStream.on('error', (e) => controller.error(e));
    },
    pull() { nodeStream.resume(); },
    cancel() { nodeStream.destroy(); },
  });
  const headers: Record<string, string> = {
    'Content-Type': opts.mime || 'application/octet-stream',
    'Cache-Control': 'no-store',
    'Accept-Ranges': 'bytes',
  };
  if (range) {
    headers['Content-Range'] = `bytes ${range.start}-${range.end}/${opts.bytes}`;
    headers['Content-Length'] = String(range.end - range.start + 1);
  } else if (opts.bytes != null) {
    headers['Content-Length'] = String(opts.bytes);
  }
  if (opts.filename) headers['Content-Disposition'] = `attachment; filename="${opts.filename.replace(/[^\w.\-]+/g, '_')}"`;
  return new Response(stream, { status: range ? 206 : 200, headers });
}
