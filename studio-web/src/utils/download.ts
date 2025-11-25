/**
 * download.ts — small, dependency-free helpers to save files/artifacts locally.
 *
 * Works in modern browsers (Chrome, Firefox, Safari, Edge).
 * Uses <a download> with Blob URLs, falls back to opening the URL when necessary.
 */

export type BytesLike = Uint8Array | ArrayBuffer | ArrayBufferView;

/** Quick environment guard (Vite SSR / tests) */
function isBrowser(): boolean {
  return typeof window !== 'undefined' && typeof document !== 'undefined';
}

/** Remove characters that are problematic across platforms */
export function sanitizeFilename(name: string): string {
  const cleaned = name.replace(/[<>:"/\\|?*\x00-\x1F]/g, ' ').replace(/\s+/g, ' ').trim();
  // Prevent hidden files or empty names
  return cleaned.length ? cleaned.replace(/^\.+/, '') : 'download';
}

/** Generate a timestamp suffix like 2025-09-29_14-03-21 */
export function timestampSuffix(d = new Date()): string {
  const iso = d.toISOString().replace(/[:.]/g, '-');
  // "2025-09-29T19-03-21-123Z" -> "2025-09-29_19-03-21"
  return iso.slice(0, 19).replace('T', '_');
}

export interface FileNameOptions {
  /**
   * Optional suffix strategy:
   *  - 'timestamp' → appends "_YYYY-MM-DD_HH-MM-SS"
   *  - string → appended as-is (with a leading underscore)
   */
  suffix?: 'timestamp' | string;
}

/** Create a safe filename with optional extension and suffix. */
export function makeFilename(base: string, ext?: string, opts: FileNameOptions = {}): string {
  const core = sanitizeFilename(base || 'download');
  const suffix =
    opts.suffix === 'timestamp'
      ? `_${timestampSuffix()}`
      : typeof opts.suffix === 'string' && opts.suffix.length
      ? `_${sanitizeFilename(opts.suffix)}`
      : '';
  const normExt = ext ? (ext.startsWith('.') ? ext : `.${ext}`) : '';
  return `${core}${suffix}${normExt}`;
}

/** Convert arbitrary bytes-like input into Uint8Array */
function toBytes(input: BytesLike): Uint8Array {
  if (input instanceof Uint8Array) return input;
  if (input instanceof ArrayBuffer) return new Uint8Array(input);
  if (ArrayBuffer.isView(input)) return new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
  throw new TypeError('toBytes: unsupported input type');
}

/** Low-level: trigger a download for a Blob with a given filename. */
export async function downloadBlob(blob: Blob, filename: string): Promise<void> {
  if (!isBrowser()) throw new Error('downloadBlob: not in a browser environment');
  const a = document.createElement('a');
  const url = URL.createObjectURL(blob);
  try {
    a.href = url;
    a.download = filename;
    // Safari needs the element in the DOM for click to work reliably
    document.body.appendChild(a);
    a.click();
  } finally {
    // Revoke URL after a tick to allow download to start
    setTimeout(() => URL.revokeObjectURL(url), 0);
    if (a.parentNode) a.parentNode.removeChild(a);
  }
}

/** Download text content as a file. */
export async function downloadText(text: string, filename: string, mime = 'text/plain;charset=utf-8'): Promise<void> {
  const blob = new Blob([text], { type: mime });
  return downloadBlob(blob, filename);
}

/** Download JSON (pretty by default). */
export async function downloadJSON(
  value: unknown,
  filename: string,
  pretty: number | boolean = 2,
  mime = 'application/json;charset=utf-8',
): Promise<void> {
  const text = JSON.stringify(value, null, pretty ? (typeof pretty === 'number' ? pretty : 2) : 0);
  return downloadText(text, filename, mime);
}

/** Download raw bytes with an optional MIME type. */
export async function downloadBytes(bytes: BytesLike, filename: string, mime = 'application/octet-stream'): Promise<void> {
  const blob = new Blob([toBytes(bytes)], { type: mime });
  return downloadBlob(blob, filename);
}

/**
 * Download from a (possibly cross-origin) URL.
 * - If same-origin or CORS allows, we fetch and save as a Blob (best UX).
 * - Otherwise, we fall back to opening the URL in a new tab (user can save manually).
 */
export async function downloadFromURL(url: string, filename?: string): Promise<void> {
  if (!isBrowser()) throw new Error('downloadFromURL: not in a browser environment');

  try {
    const resp = await fetch(url, { mode: 'cors' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const ct = resp.headers.get('content-type') || 'application/octet-stream';
    const blob = await resp.blob();
    const name = filename || inferNameFromURL(url) || makeFilename('download');
    await downloadBlob(new Blob([blob], { type: ct }), name);
  } catch {
    // Fallback: navigate to URL (browser will handle)
    const a = document.createElement('a');
    a.href = url;
    a.rel = 'noopener noreferrer';
    a.target = '_blank';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
}

/** Try to infer a readable filename from a URL path. */
function inferNameFromURL(url: string): string | null {
  try {
    const u = new URL(url, window.location.href);
    const last = u.pathname.split('/').filter(Boolean).pop();
    if (!last) return null;
    return sanitizeFilename(decodeURIComponent(last));
  } catch {
    return null;
  }
}

/** Convenience wrappers tailored for common artifact file types */

// Save an ABI JSON (pretty)
export async function saveAbiJson(abi: unknown, baseName = 'abi', opts: FileNameOptions = { suffix: 'timestamp' }) {
  const filename = makeFilename(baseName, '.json', opts);
  await downloadJSON(abi, filename);
}

// Save a contract manifest (JSON)
export async function saveManifestJson(manifest: unknown, baseName = 'manifest', opts: FileNameOptions = { suffix: 'timestamp' }) {
  const filename = makeFilename(baseName, '.json', opts);
  await downloadJSON(manifest, filename);
}

// Save compiled IR bytes (CBOR/msgpack etc.)
export async function saveIrBytes(bytes: BytesLike, baseName = 'contract', opts: FileNameOptions = { suffix: 'timestamp' }) {
  const filename = makeFilename(baseName, '.ir', opts);
  await downloadBytes(bytes, filename, 'application/octet-stream');
}

// Save a CBOR-encoded transaction
export async function saveCborTx(bytes: BytesLike, baseName = 'tx', opts: FileNameOptions = { suffix: 'timestamp' }) {
  const filename = makeFilename(baseName, '.cbor', opts);
  await downloadBytes(bytes, filename, 'application/cbor');
}

// Save a generic artifact blob with explicit content type
export async function saveArtifactBlob(
  blob: Blob,
  baseName = 'artifact',
  ext = '.bin',
  opts: FileNameOptions = { suffix: 'timestamp' },
) {
  const filename = makeFilename(baseName, ext, opts);
  await downloadBlob(blob, filename);
}

export default {
  sanitizeFilename,
  timestampSuffix,
  makeFilename,
  downloadBlob,
  downloadText,
  downloadJSON,
  downloadBytes,
  downloadFromURL,
  saveAbiJson,
  saveManifestJson,
  saveIrBytes,
  saveCborTx,
  saveArtifactBlob,
};
