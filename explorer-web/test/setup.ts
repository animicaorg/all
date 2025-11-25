/**
 * Vitest setup for explorer-web (Node environment).
 * - Polyfills fetch, Request/Response, Headers, FormData, Blob, File (via undici)
 * - Web Crypto API (subtle/sign/verify/random) via node:crypto
 * - WebSocket via 'ws'
 * - TextEncoder/TextDecoder via node:util
 * - atob/btoa helpers
 * - Stable UTC timezone for deterministic snapshots
 */

import { Buffer } from 'node:buffer';
import { TextEncoder, TextDecoder } from 'node:util';
import { webcrypto as _webcrypto } from 'node:crypto';
import WS from 'ws';

// --- fetch & friends (undici) ---
import {
  fetch as _fetch,
  Headers as _Headers,
  Request as _Request,
  Response as _Response,
  FormData as _FormData,
  File as _File,
  Blob as _Blob,
} from 'undici';

declare global {
  // eslint-disable-next-line no-var
  var TextEncoder: typeof TextEncoder;
  // eslint-disable-next-line no-var
  var TextDecoder: typeof TextDecoder;
  // Node/Vitest globals we may polyfill
  // eslint-disable-next-line no-var
  var atob: (b64: string) => string;
  // eslint-disable-next-line no-var
  var btoa: (bin: string) => string;
  // eslint-disable-next-line no-var
  var WebSocket: typeof WS;
  interface Crypto {
    subtle: SubtleCrypto;
    getRandomValues<T extends ArrayBufferView | null>(array: T): T;
  }
  // eslint-disable-next-line no-var
  var crypto: Crypto;
}

// Timezone stability for tests
process.env.TZ = 'UTC';

// TextEncoder/TextDecoder (Node)
if (!(globalThis as any).TextEncoder) globalThis.TextEncoder = TextEncoder;
if (!(globalThis as any).TextDecoder) globalThis.TextDecoder = TextDecoder as any;

// Fetch API polyfills
if (!(globalThis as any).fetch) (globalThis as any).fetch = _fetch as unknown as typeof fetch;
if (!(globalThis as any).Headers) (globalThis as any).Headers = _Headers as unknown as typeof Headers;
if (!(globalThis as any).Request) (globalThis as any).Request = _Request as unknown as typeof Request;
if (!(globalThis as any).Response) (globalThis as any).Response = _Response as unknown as typeof Response;
if (!(globalThis as any).FormData) (globalThis as any).FormData = _FormData as unknown as typeof FormData;
if (!(globalThis as any).Blob) (globalThis as any).Blob = _Blob as unknown as typeof Blob;
if (!(globalThis as any).File) (globalThis as any).File = _File as unknown as typeof File;

// WebSocket (some tests subscribe to newHeads)
if (!(globalThis as any).WebSocket) (globalThis as any).WebSocket = WS as any;

// Web Crypto (subtle + getRandomValues)
if (!(globalThis as any).crypto) (globalThis as any).crypto = _webcrypto as unknown as Crypto;

// Base64 helpers
if (!(globalThis as any).atob) {
  (globalThis as any).atob = (b64: string) => Buffer.from(b64, 'base64').toString('binary');
}
if (!(globalThis as any).btoa) {
  (globalThis as any).btoa = (bin: string) => Buffer.from(bin, 'binary').toString('base64');
}

// URL blob helpers (no-ops in Node but some code may call them)
if (!(URL as any).createObjectURL) {
  (URL as any).createObjectURL = (_: any) => 'blob:nodedummy';
}
if (!(URL as any).revokeObjectURL) {
  (URL as any).revokeObjectURL = (_: any) => {};
}

// Quiet down WebSocket "unhandled error events" in tests to avoid noisy output
// (Developers can remove this if they want strict WS error surfacing.)
process.on('unhandledRejection', (err) => {
  if (String(err).includes('WebSocket')) {
    // eslint-disable-next-line no-console
    console.warn('[vitest:ws] Suppressed unhandledRejection:', err);
    return;
  }
  throw err;
});

export {};
