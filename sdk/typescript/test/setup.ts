/* Test setup for @animica/sdk (Vitest)
 * - Brings browser-ish globals to Node (fetch, Headers, WebSocket, crypto, TextEncoder)
 * - Keeps things minimal and deterministic for unit tests.
 */

import { webcrypto as nodeWebcrypto, randomUUID } from 'node:crypto'
import { TextEncoder as NodeTextEncoder, TextDecoder as NodeTextDecoder } from 'node:util'
import WebSocketImpl from 'ws'
import {
  fetch as undiciFetch,
  Headers as UndiciHeaders,
  Request as UndiciRequest,
  Response as UndiciResponse,
  FormData as UndiciFormData,
  File as UndiciFile,
  Blob as UndiciBlob
} from 'undici'

// ──────────────────────────────────────────────────────────────────────────────
// crypto (WebCrypto-compatible)
// ──────────────────────────────────────────────────────────────────────────────
if (!(globalThis as any).crypto) {
  ;(globalThis as any).crypto = nodeWebcrypto as unknown as Crypto
} else if (!(globalThis.crypto as any).subtle && (nodeWebcrypto as any).subtle) {
  // Fill subtle if Node has it (Node >=16.15 exposes webcrypto.subtle)
  ;(globalThis.crypto as any).subtle = (nodeWebcrypto as any).subtle
}
if (!(globalThis.crypto as any).getRandomValues && (nodeWebcrypto as any).getRandomValues) {
  ;(globalThis.crypto as any).getRandomValues = (nodeWebcrypto as any).getRandomValues.bind(nodeWebcrypto)
}
if (!(globalThis.crypto as any).randomUUID && typeof randomUUID === 'function') {
  ;(globalThis.crypto as any).randomUUID = randomUUID
}

// ──────────────────────────────────────────────────────────────────────────────
/* TextEncoder/TextDecoder for Node */
// ──────────────────────────────────────────────────────────────────────────────
if (!(globalThis as any).TextEncoder) (globalThis as any).TextEncoder = NodeTextEncoder as unknown as typeof TextEncoder
if (!(globalThis as any).TextDecoder) (globalThis as any).TextDecoder = NodeTextDecoder as unknown as typeof TextDecoder

// ──────────────────────────────────────────────────────────────────────────────
/* fetch/Headers/Request/Response/FormData/File/Blob via undici */
// ──────────────────────────────────────────────────────────────────────────────
if (!(globalThis as any).fetch) (globalThis as any).fetch = undiciFetch
if (!(globalThis as any).Headers) (globalThis as any).Headers = UndiciHeaders as unknown as typeof Headers
if (!(globalThis as any).Request) (globalThis as any).Request = UndiciRequest as unknown as typeof Request
if (!(globalThis as any).Response) (globalThis as any).Response = UndiciResponse as unknown as typeof Response
if (!(globalThis as any).FormData) (globalThis as any).FormData = UndiciFormData as unknown as typeof FormData
if (!(globalThis as any).File) (globalThis as any).File = UndiciFile as unknown as typeof File
if (!(globalThis as any).Blob) (globalThis as any).Blob = UndiciBlob as unknown as typeof Blob

// ──────────────────────────────────────────────────────────────────────────────
/* WebSocket (for rpc/ws tests) */
// ──────────────────────────────────────────────────────────────────────────────
if (!(globalThis as any).WebSocket) {
  ;(globalThis as any).WebSocket = WebSocketImpl as unknown as typeof WebSocket
}

// ──────────────────────────────────────────────────────────────────────────────
/* atob/btoa for Node environments */
// ──────────────────────────────────────────────────────────────────────────────
if (!(globalThis as any).atob) {
  ;(globalThis as any).atob = (b64: string) => Buffer.from(b64, 'base64').toString('binary')
}
if (!(globalThis as any).btoa) {
  ;(globalThis as any).btoa = (bin: string) => Buffer.from(bin, 'binary').toString('base64')
}

// Quiet down noisy unhandled promise warnings in tests when intentionally closing sockets
process.on('unhandledRejection', (err) => {
  // Surface real errors; ignore WebSocket close race during teardown
  const msg = String(err?.toString?.() ?? err)
  if (!/WebSocket is not open|CLOSE_ABNORMAL|ECONNREFUSED/.test(msg)) {
    // eslint-disable-next-line no-console
    console.error('Unhandled Rejection:', err)
  }
})

export {}
