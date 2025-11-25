/**
 * Vitest global setup for Studio Web.
 * - Provides a robust fetch/Request/Response polyfill via `undici` when running in Node.
 * - Ensures `crypto.subtle`, TextEncoder/TextDecoder, Blob/File/FormData exist.
 * - Adds small shims commonly expected by browser-centric code.
 */

import { webcrypto } from 'node:crypto';
import { TextDecoder, TextEncoder } from 'node:util';

// ---- crypto (WebCrypto) ----
if (typeof globalThis.crypto === 'undefined' || !('subtle' in globalThis.crypto)) {
  // @ts-expect-error assigning readonly in Node test env is OK
  globalThis.crypto = webcrypto as unknown as Crypto;
}

// ---- fetch / Headers / Request / Response / Blob / File / FormData ----
// Vitest (Node) doesn't always expose fetch depending on env. Use undici when missing.
(async () => {
  if (typeof globalThis.fetch === 'undefined') {
    const undici = await import('undici');
    const { fetch, Headers, Request, Response, FormData, File, Blob } = undici as unknown as {
      fetch: typeof globalThis.fetch;
      Headers: typeof globalThis.Headers;
      Request: typeof globalThis.Request;
      Response: typeof globalThis.Response;
      FormData: typeof globalThis.FormData;
      File: typeof globalThis.File;
      Blob: typeof globalThis.Blob;
    };

    Object.assign(globalThis, { fetch, Headers, Request, Response, FormData, File, Blob });
  } else {
    // Some jsdom environments have fetch but not Blob/File/FormData; try to fill gaps via undici
    const needBlob = typeof globalThis.Blob === 'undefined';
    const needFile = typeof globalThis.File === 'undefined';
    const needForm = typeof globalThis.FormData === 'undefined';
    if (needBlob || needFile || needForm) {
      const undici = await import('undici');
      if (needBlob) Object.assign(globalThis, { Blob: (undici as any).Blob as typeof Blob });
      if (needFile) Object.assign(globalThis, { File: (undici as any).File as typeof File });
      if (needForm) Object.assign(globalThis, { FormData: (undici as any).FormData as typeof FormData });
    }
  }
})().catch((err) => {
  // eslint-disable-next-line no-console
  console.warn('[test/setup] undici polyfill failed (tests may provide their own):', err);
});

// ---- encoders ----
if (typeof globalThis.TextEncoder === 'undefined') {
  // @ts-expect-error assigning in test env
  globalThis.TextEncoder = TextEncoder as unknown as typeof globalThis.TextEncoder;
}
if (typeof globalThis.TextDecoder === 'undefined') {
  // @ts-expect-error assigning in test env
  globalThis.TextDecoder = TextDecoder as unknown as typeof globalThis.TextDecoder;
}

// ---- atob / btoa ----
if (typeof globalThis.atob === 'undefined') {
  globalThis.atob = (b64: string): string => Buffer.from(b64, 'base64').toString('binary');
}
if (typeof globalThis.btoa === 'undefined') {
  globalThis.btoa = (bin: string): string => Buffer.from(bin, 'binary').toString('base64');
}

// ---- ReadableStream (Node 18+) ----
if (typeof globalThis.ReadableStream === 'undefined') {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const streams = require('node:stream/web');
    if (streams?.ReadableStream) {
      // @ts-expect-error assigning in test env
      globalThis.ReadableStream = streams.ReadableStream;
    }
  } catch {
    // ignore
  }
}

// ---- Minor sanity: fail tests on unhandled rejections to surface issues early ----
process.on('unhandledRejection', (reason) => {
  // eslint-disable-next-line no-console
  console.error('Unhandled Promise Rejection in tests:', reason);
  // Throwing will fail the current test run; comment out if you prefer warnings.
  throw reason;
});

export {};
