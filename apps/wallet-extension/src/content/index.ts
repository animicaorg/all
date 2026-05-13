// Content script - injects provider into page context.
//
// MV3 content scripts can't statically import shared chunks (the bundler
// would split safeJson.ts out and the runtime can't fetch it), so the
// BigInt/Uint8Array sanitizer is duplicated inline below. Keep the
// algorithm in sync with src/core/rpc/safeJson.ts:toJsonSafe('rpc').
function sanitizeForJsonRpc(value: unknown): unknown {
  if (typeof value === 'bigint') return value.toString(10);
  if (value instanceof Uint8Array) {
    let hex = '0x';
    for (const byte of value) hex += byte.toString(16).padStart(2, '0');
    return hex;
  }
  if (typeof ArrayBuffer !== 'undefined' && value instanceof ArrayBuffer) {
    return sanitizeForJsonRpc(new Uint8Array(value));
  }
  if (
    typeof ArrayBuffer !== 'undefined' &&
    value &&
    typeof value === 'object' &&
    ArrayBuffer.isView(value as ArrayBufferView) &&
    !(value instanceof Uint8Array)
  ) {
    const view = value as ArrayBufferView;
    return sanitizeForJsonRpc(new Uint8Array(view.buffer, view.byteOffset, view.byteLength));
  }
  if (Array.isArray(value)) return value.map((item) => sanitizeForJsonRpc(item));
  if (value && typeof value === 'object') {
    if (value instanceof Date) return value.toISOString();
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = sanitizeForJsonRpc(v);
    }
    return out;
  }
  if (typeof value === 'number' && !Number.isFinite(value)) return null;
  return value;
}

const script = document.createElement('script');
script.src = chrome.runtime.getURL('provider.js');
script.onload = function() {
  (this as HTMLScriptElement).remove();
};
(document.head || document.documentElement).appendChild(script);

// Bridge messages between page and background.
//
// chrome.runtime.sendMessage requires the payload to be JSON-cloneable, so
// any BigInt or Uint8Array values a dapp passes in `params` (e.g. an
// ethers v6 tx object with `value: 100n`) would otherwise crash with
// "Do not know how to serialize a BigInt" before the wallet ever sees the
// request. Route the params through toJsonSafe so BigInt -> decimal
// string, Uint8Array -> 0x-hex, consistently. The background handlers
// already accept both shapes.
window.addEventListener('message', async (event) => {
  if (event.source !== window) return;
  if (event.data.type !== 'ANIMICA_PROVIDER_REQUEST') return;

  const { id, method, params } = event.data;

  try {
    const response = await chrome.runtime.sendMessage({
      method,
      params: sanitizeForJsonRpc(params),
      origin: window.location.origin,
      href: window.location.href,
    });

    if (response?.error) {
      const error = response.error;
      window.postMessage({
        type: 'ANIMICA_PROVIDER_RESPONSE',
        id,
        error: {
          code: typeof error.code === 'number' ? error.code : -32603,
          message: typeof error.message === 'string' ? error.message : 'Internal error',
          data: error.data,
        },
      }, '*');
      return;
    }

    window.postMessage({
      type: 'ANIMICA_PROVIDER_RESPONSE',
      id,
      result: response,
    }, '*');
  } catch (error: any) {
    window.postMessage({
      type: 'ANIMICA_PROVIDER_RESPONSE',
      id,
      error: {
        code: -32603,
        message: error.message || 'Internal error',
      },
    }, '*');
  }
});
