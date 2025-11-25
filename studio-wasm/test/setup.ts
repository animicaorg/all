/**
 * Vitest setup for studio-wasm.
 * - Provides a fetch/Request/Response/Headers implementation (undici) for Node.
 * - Ensures TextEncoder/TextDecoder, Blob, and webcrypto are present on globalThis.
 * - Adds tiny atob/btoa shims for parity with browsers.
 *
 * This file is referenced from vitest.config.ts via `setupFiles`.
 */

import { fetch as undiciFetch, Headers as UndiciHeaders, Request as UndiciRequest, Response as UndiciResponse } from 'undici';
import { webcrypto } from 'node:crypto';
import { TextEncoder as NodeTextEncoder, TextDecoder as NodeTextDecoder } from 'node:util';
import { Blob as NodeBlob, Buffer } from 'node:buffer';

// fetch + friends
if (!globalThis.fetch) globalThis.fetch = undiciFetch as unknown as typeof fetch;
if (!globalThis.Headers) globalThis.Headers = UndiciHeaders as unknown as typeof Headers;
if (!globalThis.Request) globalThis.Request = UndiciRequest as unknown as typeof Request;
if (!globalThis.Response) globalThis.Response = UndiciResponse as unknown as typeof Response;

// webcrypto
if (!globalThis.crypto) {
  // Node's WHATWG-compliant Web Crypto
  // @ts-expect-error - assigning readonly in Node types is safe for test env
  globalThis.crypto = webcrypto as unknown as Crypto;
}

// TextEncoder/TextDecoder
// @ts-expect-error - safe for test bootstrap
if (!globalThis.TextEncoder) globalThis.TextEncoder = NodeTextEncoder as unknown as typeof TextEncoder;
// @ts-expect-error - safe for test bootstrap
if (!globalThis.TextDecoder) globalThis.TextDecoder = NodeTextDecoder as unknown as typeof TextDecoder;

// Blob (Node >=18 provides global Blob; this is a fallback)
if (!('Blob' in globalThis)) {
  // @ts-expect-error - safe polyfill for tests
  globalThis.Blob = NodeBlob as unknown as typeof Blob;
}

// atob/btoa polyfills (used occasionally by helpers)
if (!(globalThis as any).atob) {
  (globalThis as any).atob = (b64: string) => Buffer.from(b64, 'base64').toString('binary');
}
if (!(globalThis as any).btoa) {
  (globalThis as any).btoa = (str: string) => Buffer.from(str, 'binary').toString('base64');
}

// No exports — this file is executed for its side effects during test bootstrap.
export {};
