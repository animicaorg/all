import { encode as cborEncode } from 'cbor-x';
import { getEffectiveRpcUrl } from './rpcConfig';
import { runDiagnosticsBundle, type DiagnosticsBundle } from './diagnostics';
import { discoverMethods, healthProbe, rpcCall, type RpcCallOutcome } from './rpcClient';

const DEFAULT_RPC_URL = 'https://mainnet.animica.org/rpc';
const RETRIABLE_DIAGNOSTIC_CODES = new Set([-32603, -32602, -32010, -32011, -32012]);

export type SendAttempt = {
  method: string;
  shape: 'objectArray' | 'array';
  ok: boolean;
  code?: number | 'RPC_ERROR_UNKNOWN';
  message?: string;
};

export type StructuredSendError = {
  code: number | string;
  message: string;
  rpcUrl: string;
  chainIdExpected?: number;
  chainIdActual?: number;
  attempts: SendAttempt[];
  diagnostics: DiagnosticsBundle;
  correlationId: string;
};

export type SendRawTxSuccess = {
  ok: true;
  txHash: string;
  confirmed: boolean;
  status?: unknown;
  correlationId: string;
  attempts: SendAttempt[];
};

export type SendRawTxFailure = {
  ok: false;
  error: StructuredSendError;
};

export type SendRawTxResult = SendRawTxSuccess | SendRawTxFailure;

function isDebugTxEnabled(): boolean {
  const g = globalThis as Record<string, unknown>;
  const flag = g.__ANIMICA_DEBUG_TX__;
  const env = (import.meta as any)?.env?.DEBUG_TX;
  return flag === true || flag === '1' || env === '1';
}

function previewRawTx(rawTx: string): string {
  if (isDebugTxEnabled()) return rawTx;
  return `${rawTx.slice(0, 26)}...`;
}

function correlationId(): string {
  const random = Math.random().toString(36).slice(2, 10);
  return `${Date.now().toString(36)}-${random}`;
}

function normalizeRawTx(input: unknown): string {
  if (typeof input === 'string') {
    const trimmed = input.trim();
    if (!trimmed.startsWith('0x') && !trimmed.startsWith('0X')) {
      throw new Error('CLIENT_INVALID_RAWTX: missing 0x prefix');
    }
    const body = trimmed.slice(2);
    if (!/^[0-9a-f]*$/i.test(body)) throw new Error('CLIENT_INVALID_RAWTX: not hex');
    if (body.length < 4) throw new Error('CLIENT_INVALID_RAWTX: payload too short');
    if (body.length % 2 !== 0) throw new Error('CLIENT_INVALID_RAWTX: hex length must be even');
    return `0x${body.toLowerCase()}`;
  }

  if (input instanceof Uint8Array) {
    const hex = Array.from(input).map((b) => b.toString(16).padStart(2, '0')).join('');
    if (hex.length < 4) throw new Error('CLIENT_INVALID_RAWTX: payload too short');
    return `0x${hex}`;
  }

  if (input instanceof ArrayBuffer) {
    return normalizeRawTx(new Uint8Array(input));
  }

  if (input && typeof input === 'object') {
    const encoded = cborEncode(input);
    return normalizeRawTx(encoded);
  }

  throw new Error('CLIENT_INVALID_RAWTX: unsupported rawTx input type');
}

function extractTxHash(value: unknown): string | undefined {
  if (typeof value === 'string' && value.startsWith('0x')) return value;
  if (!value || typeof value !== 'object') return undefined;
  const obj = value as Record<string, unknown>;
  for (const key of ['txHash', 'transactionHash', 'hash', 'txid']) {
    const maybe = obj[key];
    if (typeof maybe === 'string' && maybe.startsWith('0x')) return maybe;
  }
  return undefined;
}

function buildBaseAttempts(methodsDiscovered: string[]): Array<{ method: string; shape: 'objectArray' | 'array' }> {
  const fixed: Array<{ method: string; shape: 'objectArray' | 'array' }> = [
    { method: 'tx.sendRawTransaction', shape: 'objectArray' },
    { method: 'tx_sendRawTransaction', shape: 'objectArray' },
    { method: 'tx.submitRawTransaction', shape: 'objectArray' },
    { method: 'tx2.sendRawTransaction', shape: 'objectArray' },
    { method: 'tx.sendRawTransaction', shape: 'array' },
    { method: 'tx_sendRawTransaction', shape: 'array' },
    { method: 'tx.submitRawTransaction', shape: 'array' },
    { method: 'tx2.sendRawTransaction', shape: 'array' },
  ];

  const discoverLike = methodsDiscovered.filter((m) => /(tx(\.|_)sendRawTransaction|tx(\.|_)submitRawTransaction|tx2(\.|_)sendRawTransaction)/.test(m));
  const all = [...fixed];
  for (const m of discoverLike) {
    all.push({ method: m, shape: 'objectArray' });
    all.push({ method: m, shape: 'array' });
  }

  const seen = new Set<string>();
  return all.filter((entry) => {
    const key = `${entry.method}::${entry.shape}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

async function callWithShape(rpcUrl: string, method: string, rawTx: string, shape: 'objectArray' | 'array', timeoutMs: number): Promise<RpcCallOutcome> {
  const params = shape === 'objectArray' ? [{ rawTx }] : [rawTx];
  return rpcCall(rpcUrl, method, params, { timeoutMs, retryNetworkOnce: true });
}

async function preflight(rpcUrl: string, methods: string[], rawTx: string, timeoutMs: number): Promise<{ ok: boolean; details: DiagnosticsBundle }> {
  const diagnostics: DiagnosticsBundle = {};

  const verifyCandidates = ['tx.debugVerifyRawTransaction', 'tx_debugVerifyRawTransaction'].filter((m) => methods.includes(m));
  if (verifyCandidates.length > 0) {
    diagnostics.verify = [];
    for (const method of verifyCandidates) {
      for (const shape of ['objectArray', 'array'] as const) {
        const out = await callWithShape(rpcUrl, method, rawTx, shape, timeoutMs);
        if (out.ok) {
          diagnostics.verify.push({ ok: true, method, paramsShape: shape, response: out.response?.result });
          break;
        }
        diagnostics.verify.push({
          ok: false,
          method,
          paramsShape: shape,
          error: { code: typeof out.response?.error?.code === 'number' ? out.response.error.code : 'RPC_ERROR_UNKNOWN', message: out.response?.error?.message ?? out.networkError ?? out.protocolError ?? 'RPC call failed', data: out.response?.error?.data },
          response: out.response,
        });
        if (out.response?.error?.code !== -32602 && out.response?.error?.code !== -32601) return { ok: false, details: diagnostics };
      }
    }
  }

  const decodeCandidates = ['tx.decodeRawTransaction', 'tx_decodeRawTransaction'].filter((m) => methods.includes(m));
  if (decodeCandidates.length > 0) {
    diagnostics.decode = [];
    for (const method of decodeCandidates) {
      for (const shape of ['objectArray', 'array'] as const) {
        const out = await callWithShape(rpcUrl, method, rawTx, shape, timeoutMs);
        if (out.ok) {
          diagnostics.decode.push({ ok: true, method, paramsShape: shape, response: out.response?.result });
          break;
        }
        diagnostics.decode.push({
          ok: false,
          method,
          paramsShape: shape,
          error: { code: typeof out.response?.error?.code === 'number' ? out.response.error.code : 'RPC_ERROR_UNKNOWN', message: out.response?.error?.message ?? out.networkError ?? out.protocolError ?? 'RPC call failed', data: out.response?.error?.data },
          response: out.response,
        });
        if (out.response?.error?.code !== -32602 && out.response?.error?.code !== -32601) return { ok: false, details: diagnostics };
      }
    }
  }

  return { ok: true, details: diagnostics };
}

async function postVerify(rpcUrl: string, methods: string[], txHash: string, timeoutMs: number): Promise<{ confirmed: boolean; status?: unknown }> {
  const checks = ['tx.getTransactionByHash', 'tx_getTransactionByHash', 'tx.getTransactionStatus', 'tx.getStatus', 'tx.status']
    .filter((method) => methods.includes(method));

  let lastStatus: unknown;
  const started = Date.now();
  let delayMs = 400;

  while (Date.now() - started < 12_000) {
    for (const method of checks) {
      const out = await rpcCall(rpcUrl, method, [txHash], { timeoutMs, retryNetworkOnce: true });
      if (!out.ok) continue;
      const result = out.response?.result;
      lastStatus = result;
      if (result && result !== 'not_found') return { confirmed: true, status: result };
    }
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    delayMs = Math.min(Math.floor(delayMs * 1.7), 2800);
  }

  return { confirmed: false, status: lastStatus };
}

export async function sendRawTxPipeline(input: {
  rawTx: unknown;
  rpcUrl?: string;
  chainIdExpected?: number;
  fromAddress?: string;
  timeoutMs?: number;
}): Promise<SendRawTxResult> {
  const corr = correlationId();
  const timeoutMs = input.timeoutMs ?? 20_000;
  const rpcUrl = input.rpcUrl ?? getEffectiveRpcUrl(DEFAULT_RPC_URL);

  let normalized: string;
  try {
    normalized = normalizeRawTx(input.rawTx);
  } catch (error) {
    return {
      ok: false,
      error: {
        code: 'CLIENT_INVALID_RAWTX',
        message: error instanceof Error ? error.message : String(error),
        rpcUrl,
        chainIdExpected: input.chainIdExpected,
        attempts: [],
        diagnostics: {},
        correlationId: corr,
      },
    };
  }

  const probe = await healthProbe(rpcUrl, 8_000);
  if (!probe.ok) {
    return {
      ok: false,
      error: {
        code: 'RPC_UNREACHABLE',
        message: probe.response?.error?.message ?? probe.networkError ?? probe.protocolError ?? 'RPC health probe failed',
        rpcUrl,
        chainIdExpected: input.chainIdExpected,
        attempts: [],
        diagnostics: { status: [{ ok: false, method: probe.method, paramsShape: 'none', error: { code: typeof probe.response?.error?.code === 'number' ? probe.response.error.code : 'RPC_ERROR_UNKNOWN', message: probe.response?.error?.message ?? probe.networkError ?? probe.protocolError ?? 'Probe failed' } }] },
        correlationId: corr,
      },
    };
  }

  const methods = await discoverMethods(rpcUrl, timeoutMs).catch(() => [] as string[]);

  let chainIdActual: number | undefined;
  if (methods.includes('chain.getChainId') || methods.includes('chain_getChainId')) {
    const method = methods.includes('chain.getChainId') ? 'chain.getChainId' : 'chain_getChainId';
    const chainResp = await rpcCall(rpcUrl, method, [], { timeoutMs, retryNetworkOnce: true });
    if (chainResp.ok && chainResp.response?.result !== undefined) {
      const val = chainResp.response.result;
      if (typeof val === 'number') chainIdActual = Math.trunc(val);
      else if (typeof val === 'string') chainIdActual = Number(val.startsWith('0x') ? BigInt(val) : BigInt(val));
    }
  }

  const pre = await preflight(rpcUrl, methods, normalized, timeoutMs);
  if (!pre.ok) {
    return {
      ok: false,
      error: {
        code: 'PREFLIGHT_VERIFY_FAILED',
        message: 'Raw transaction preflight failed',
        rpcUrl,
        chainIdExpected: input.chainIdExpected,
        chainIdActual,
        attempts: [],
        diagnostics: pre.details,
        correlationId: corr,
      },
    };
  }

  const attempts: SendAttempt[] = [];
  const matrix = buildBaseAttempts(methods);
  let txHash: string | undefined;
  let lastCode: number | 'RPC_ERROR_UNKNOWN' = 'RPC_ERROR_UNKNOWN';
  let lastMessage = 'All send attempts failed';

  console.info('[wallet-extension][sendRawTx] begin', { correlationId: corr, rpcUrl, rawTxPreview: previewRawTx(normalized), attempts: matrix.length });

  for (const variant of matrix) {
    const first = await callWithShape(rpcUrl, variant.method, normalized, variant.shape, timeoutMs);
    if (first.ok) {
      txHash = extractTxHash(first.response?.result);
      if (txHash) {
        attempts.push({ method: variant.method, shape: variant.shape, ok: true });
        break;
      }
      attempts.push({ method: variant.method, shape: variant.shape, ok: false, code: 'RPC_ERROR_UNKNOWN', message: 'RPC success response missing txHash' });
      continue;
    }

    const code = typeof first.response?.error?.code === 'number' ? first.response.error.code : 'RPC_ERROR_UNKNOWN';
    const message = first.response?.error?.message ?? first.networkError ?? first.protocolError ?? 'RPC call failed';
    attempts.push({ method: variant.method, shape: variant.shape, ok: false, code, message });
    lastCode = code;
    lastMessage = message;

    if (code === -32603) {
      const second = await callWithShape(rpcUrl, variant.method, normalized, variant.shape, timeoutMs);
      if (second.ok) {
        txHash = extractTxHash(second.response?.result);
        attempts.push({ method: variant.method, shape: variant.shape, ok: true });
        if (txHash) break;
      } else {
        attempts.push({
          method: variant.method,
          shape: variant.shape,
          ok: false,
          code: typeof second.response?.error?.code === 'number' ? second.response.error.code : 'RPC_ERROR_UNKNOWN',
          message: second.response?.error?.message ?? second.networkError ?? second.protocolError ?? 'RPC call failed',
        });
      }
    }
  }

  if (!txHash) {
    const shouldDiag = typeof lastCode === 'number' ? RETRIABLE_DIAGNOSTIC_CODES.has(lastCode) : true;
    const diagnostics = shouldDiag
      ? await runDiagnosticsBundle({ rpcUrl, rawTx: normalized, fromAddress: input.fromAddress, timeoutMs }).catch((error) => ({ discover: { error: error instanceof Error ? error.message : String(error) } }))
      : {};

    return {
      ok: false,
      error: {
        code: lastCode,
        message: lastMessage,
        rpcUrl,
        chainIdExpected: input.chainIdExpected,
        chainIdActual,
        attempts,
        diagnostics,
        correlationId: corr,
      },
    };
  }

  const post = await postVerify(rpcUrl, methods, txHash, timeoutMs);
  return {
    ok: true,
    txHash,
    confirmed: post.confirmed,
    status: post.status,
    correlationId: corr,
    attempts,
  };
}
