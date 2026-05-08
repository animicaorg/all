import { encode as cborEncode, decode as cborDecode } from 'cbor-x';
import { getEffectiveRpcUrl } from './rpcConfig';
import { runDiagnosticsBundle, type DiagnosticsBundle } from './diagnostics';
import { discoverMethods, healthProbe, rpcCall, type RpcCallOutcome } from './rpcClient';

const DEFAULT_RPC_URL = 'https://rpc.animica.org/rpc';
const RETRIABLE_DIAGNOSTIC_CODES = new Set([-32603, -32602, -32010, -32011, -32012]);
const CAPABILITY_TTL_MS = 5 * 60_000;

const SCHEME_NAMES: Record<number, string> = {
  1: 'Dilithium3',
  2: 'SPHINCS+ SHAKE-128s',
  3: 'SPHINCS+ SHAKE-128f',
  4: 'SPHINCS+ SHAKE-256s',
};

export type SendAttempt = {
  method: string;
  shape: SendParamShape;
  ok: boolean;
  code?: number | 'RPC_ERROR_UNKNOWN';
  message?: string;
};

type SendParamShape = 'array' | 'object' | 'objectArray' | 'txObject' | 'txObjectArray';

export type SchemeDescriptor = { id: number; name: string };

export type SignaturePolicyError = {
  code: number | string;
  message: string;
  chainIdExpected?: number;
  chainIdActual?: number;
  rpcUrl: string;
  schemeUsed: SchemeDescriptor;
  allowedSchemes: SchemeDescriptor[];
  action: 'SWITCH_ACCOUNT_OR_ENABLE_POLICY';
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
  signaturePolicyError?: SignaturePolicyError;
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

type CachedCapabilities = {
  allowedSchemes: SchemeDescriptor[];
  defaultSchemeId?: number;
  updatedAt: number;
};

const capabilitiesCache = new Map<string, CachedCapabilities>();

function cacheKey(rpcUrl: string, chainId: number | undefined): string {
  return `${rpcUrl}::${chainId ?? 'unknown'}`;
}

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

function extractSchemeIdFromDecodedEnvelope(decodedResult: unknown): number | undefined {
  if (!decodedResult || typeof decodedResult !== 'object') return undefined;
  const decoded = decodedResult as Record<string, unknown>;
  const directAuth = decoded.auth;
  if (directAuth && typeof directAuth === 'object') {
    const auth = directAuth as Record<string, unknown>;
    const rawScheme = auth.scheme_id ?? auth.schemeId;
    if (typeof rawScheme === 'number') return Math.trunc(rawScheme);
    if (typeof rawScheme === 'string' && rawScheme.trim()) return Number(rawScheme);
  }
  return undefined;
}

function extractSchemeIdFromRawTx(rawTx: string): number | undefined {
  try {
    const hex = rawTx.slice(2);
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < hex.length; i += 2) bytes[i / 2] = parseInt(hex.slice(i, i + 2), 16);
    const decoded = cborDecode(bytes) as Record<string, unknown>;
    return extractSchemeIdFromDecodedEnvelope(decoded);
  } catch {
    return undefined;
  }
}

function describeScheme(id: number | undefined): SchemeDescriptor {
  if (typeof id !== 'number' || !Number.isFinite(id)) {
    return { id: -1, name: 'unknown' };
  }
  return { id, name: SCHEME_NAMES[id] ?? `scheme_${id}` };
}

function buildUserFacingPolicyMessage(schemeUsed: SchemeDescriptor, allowedSchemes: SchemeDescriptor[]): string {
  const allowedLabel = allowedSchemes.length > 0
    ? allowedSchemes.map((s) => `${s.name} (${s.id})`).join(', ')
    : 'unknown (policy could not be read)';
  return (
    `This network currently disables ${schemeUsed.name} (${schemeUsed.id}). ` +
    `Create/switch to an account using one of: ${allowedLabel}, or ask the network operator to enable it.`
  );
}

function isPolicyDisabledError(code: number | string, message: string): boolean {
  const normalized = message.toLowerCase();
  return code === -32010
    ? normalized.includes('signature scheme disabled by policy') || normalized.includes('scheme_unsupported')
    : normalized.includes('scheme_unsupported') || normalized.includes('signature scheme disabled by policy');
}

function parseChainId(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === 'string' && value.trim()) {
    try {
      const v = value.startsWith('0x') ? BigInt(value) : BigInt(value);
      return Number(v);
    } catch {
      return undefined;
    }
  }
  return undefined;
}

async function tryMethodShapes(rpcUrl: string, method: string, rawTx: string, timeoutMs: number): Promise<RpcCallOutcome[]> {
  const shapes: Array<'array' | 'object' | 'objectArray'> = ['array', 'object', 'objectArray'];
  const outcomes: RpcCallOutcome[] = [];
  for (const shape of shapes) {
    const params = shape === 'array' ? [rawTx] : shape === 'object' ? { rawTx } : [{ rawTx }];
    const out = await rpcCall(rpcUrl, method, params, { timeoutMs, retryNetworkOnce: true });
    outcomes.push(out);
    if (out.ok) return outcomes;
    const code = out.response?.error?.code;
    if (code === -32601) break;
  }
  return outcomes;
}

async function detectSchemeFromDecode(rpcUrl: string, rawTx: string, timeoutMs: number): Promise<number | undefined> {
  const methods = ['tx.decodeRawTransaction', 'tx_decodeRawTransaction'];
  for (const method of methods) {
    const outcomes = await tryMethodShapes(rpcUrl, method, rawTx, timeoutMs);
    for (const out of outcomes) {
      if (out.ok) {
        const schemeId = extractSchemeIdFromDecodedEnvelope(out.response?.result);
        if (schemeId !== undefined) return schemeId;
        break;
      }
      if (out.response?.error?.code !== -32601 && out.response?.error?.code !== -32602) {
        break;
      }
    }
  }
  return undefined;
}

async function debugVerifyForPolicy(rpcUrl: string, rawTx: string, timeoutMs: number): Promise<{ blocked: boolean; code?: number | string; message?: string }> {
  const methods = ['tx.debugVerifyRawTransaction', 'tx_debugVerifyRawTransaction'];
  for (const method of methods) {
    const outcomes = await tryMethodShapes(rpcUrl, method, rawTx, timeoutMs);
    for (const out of outcomes) {
      if (out.ok) return { blocked: false };
      const code = typeof out.response?.error?.code === 'number' ? out.response.error.code : 'RPC_ERROR_UNKNOWN';
      const message = out.response?.error?.message ?? out.networkError ?? out.protocolError ?? 'RPC call failed';
      if (isPolicyDisabledError(code, message)) return { blocked: true, code, message };
      if (out.response?.error?.code !== -32601 && out.response?.error?.code !== -32602) {
        return { blocked: false };
      }
    }
  }
  return { blocked: false };
}

async function getAllowedSchemesFromPolicyMethod(rpcUrl: string, timeoutMs: number): Promise<{ chainId?: number; allowedSchemes: SchemeDescriptor[]; defaultSchemeId?: number } | null> {
  const out = await rpcCall(rpcUrl, 'policy.getPqAlgPolicy', [], { timeoutMs, retryNetworkOnce: true });
  if (!out.ok || !out.response?.result || typeof out.response.result !== 'object') return null;
  const result = out.response.result as Record<string, unknown>;
  const rawAllowed = Array.isArray(result.allowedSchemes) ? result.allowedSchemes : [];
  const allowedSchemes: SchemeDescriptor[] = rawAllowed
    .map((entry) => {
      if (!entry || typeof entry !== 'object') return null;
      const obj = entry as Record<string, unknown>;
      const enabled = obj.enabled;
      if (enabled === false) return null;
      const id = parseChainId(obj.id);
      if (id === undefined) return null;
      const name = typeof obj.name === 'string' && obj.name.length > 0 ? obj.name : describeScheme(id).name;
      return { id, name } satisfies SchemeDescriptor;
    })
    .filter((v): v is SchemeDescriptor => !!v);

  return {
    chainId: parseChainId(result.chainId),
    defaultSchemeId: parseChainId(result.defaultSchemeId),
    allowedSchemes,
  };
}

function extractAllowedFromChainParams(params: Record<string, unknown>): { allowedSchemes: SchemeDescriptor[]; defaultSchemeId?: number } {
  const policy = (params.policy ?? params.sigPolicy ?? params.signaturePolicy) as Record<string, unknown> | undefined;
  const rawAllowed = (policy?.allowedSchemes ?? params.allowedSchemes) as unknown;
  const rawDefault = policy?.defaultSchemeId ?? params.defaultSchemeId;

  const allowedIds: number[] = [];
  if (Array.isArray(rawAllowed)) {
    for (const entry of rawAllowed) {
      if (typeof entry === 'number') allowedIds.push(Math.trunc(entry));
      else if (typeof entry === 'string') {
        const parsed = parseChainId(entry);
        if (parsed !== undefined) allowedIds.push(parsed);
      } else if (entry && typeof entry === 'object') {
        const id = parseChainId((entry as Record<string, unknown>).id);
        const enabled = (entry as Record<string, unknown>).enabled;
        if (id !== undefined && enabled !== false) allowedIds.push(id);
      }
    }
  }

  const dedup = Array.from(new Set(allowedIds)).sort((a, b) => a - b);
  return {
    allowedSchemes: dedup.map((id) => describeScheme(id)),
    defaultSchemeId: parseChainId(rawDefault),
  };
}

async function getAllowedSchemesFromChainParams(rpcUrl: string, timeoutMs: number): Promise<{ allowedSchemes: SchemeDescriptor[]; defaultSchemeId?: number } | null> {
  for (const method of ['chain_getParams', 'chain.getParams']) {
    const out = await rpcCall(rpcUrl, method, [], { timeoutMs, retryNetworkOnce: true });
    if (!out.ok || !out.response?.result || typeof out.response.result !== 'object') continue;
    const parsed = extractAllowedFromChainParams(out.response.result as Record<string, unknown>);
    if (parsed.allowedSchemes.length > 0 || parsed.defaultSchemeId !== undefined) return parsed;
  }
  return null;
}

async function getChainId(rpcUrl: string, methodsHint: string[], timeoutMs: number): Promise<number | undefined> {
  const candidates = methodsHint.includes('chain.getChainId') || methodsHint.includes('chain_getChainId')
    ? [methodsHint.includes('chain.getChainId') ? 'chain.getChainId' : 'chain_getChainId']
    : ['chain.getChainId', 'chain_getChainId'];

  for (const method of candidates) {
    const out = await rpcCall(rpcUrl, method, [], { timeoutMs, retryNetworkOnce: true });
    if (out.ok) {
      const parsed = parseChainId(out.response?.result);
      if (parsed !== undefined) return parsed;
    }
  }
  return undefined;
}

async function refreshCapabilitiesCache(rpcUrl: string, chainId: number | undefined, timeoutMs: number): Promise<CachedCapabilities> {
  const fromPolicy = await getAllowedSchemesFromPolicyMethod(rpcUrl, timeoutMs);
  const fromParams = fromPolicy ? null : await getAllowedSchemesFromChainParams(rpcUrl, timeoutMs);

  const allowedSchemes = fromPolicy?.allowedSchemes ?? fromParams?.allowedSchemes ?? [];
  const defaultSchemeId = fromPolicy?.defaultSchemeId ?? fromParams?.defaultSchemeId;

  const capabilities: CachedCapabilities = {
    allowedSchemes,
    defaultSchemeId,
    updatedAt: Date.now(),
  };

  capabilitiesCache.set(cacheKey(rpcUrl, chainId), capabilities);
  return capabilities;
}

async function getCapabilities(rpcUrl: string, chainId: number | undefined, timeoutMs: number, forceRefresh = false): Promise<CachedCapabilities> {
  const key = cacheKey(rpcUrl, chainId);
  const cached = capabilitiesCache.get(key);
  if (!forceRefresh && cached && Date.now() - cached.updatedAt < CAPABILITY_TTL_MS) return cached;
  return refreshCapabilitiesCache(rpcUrl, chainId, timeoutMs);
}

export async function warmPolicyCapabilities(rpcUrl: string, chainId?: number, timeoutMs: number = 20_000): Promise<void> {
  await getCapabilities(rpcUrl, chainId, timeoutMs, true);
}

function buildBaseAttempts(methodsDiscovered: string[]): Array<{ method: string; shape: SendParamShape }> {
  const fixed: Array<{ method: string; shape: SendParamShape }> = [
    { method: 'tx_sendRawTransaction', shape: 'array' },
    { method: 'tx.sendRawTransaction', shape: 'array' },
    { method: 'tx2.sendRawTransaction', shape: 'array' },
    { method: 'tx.submitRawTransaction', shape: 'array' },
    { method: 'tx_sendRawTransaction', shape: 'object' },
    { method: 'tx.sendRawTransaction', shape: 'object' },
    { method: 'tx2.sendRawTransaction', shape: 'object' },
    { method: 'tx.submitRawTransaction', shape: 'object' },
    { method: 'tx_sendRawTransaction', shape: 'objectArray' },
    { method: 'tx.sendRawTransaction', shape: 'objectArray' },
    { method: 'tx2.sendRawTransaction', shape: 'objectArray' },
    { method: 'tx.submitRawTransaction', shape: 'objectArray' },
    { method: 'tx_sendRawTransaction', shape: 'txObject' },
    { method: 'tx.sendRawTransaction', shape: 'txObject' },
    { method: 'tx2.sendRawTransaction', shape: 'txObject' },
    { method: 'tx.submitRawTransaction', shape: 'txObject' },
    { method: 'tx_sendRawTransaction', shape: 'txObjectArray' },
    { method: 'tx.sendRawTransaction', shape: 'txObjectArray' },
    { method: 'tx2.sendRawTransaction', shape: 'txObjectArray' },
    { method: 'tx.submitRawTransaction', shape: 'txObjectArray' },
  ];

  const discoverLike = methodsDiscovered.filter((m) => /(tx(\.|_)sendRawTransaction|tx(\.|_)submitRawTransaction|tx2(\.|_)sendRawTransaction)/.test(m));
  const all = [...fixed];
  for (const m of discoverLike) {
    all.push({ method: m, shape: 'array' });
    all.push({ method: m, shape: 'object' });
    all.push({ method: m, shape: 'objectArray' });
    all.push({ method: m, shape: 'txObject' });
    all.push({ method: m, shape: 'txObjectArray' });
  }

  const seen = new Set<string>();
  return all.filter((entry) => {
    const key = `${entry.method}::${entry.shape}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

async function callWithShape(rpcUrl: string, method: string, rawTx: string, shape: SendParamShape, timeoutMs: number): Promise<RpcCallOutcome> {
  const params =
    shape === 'array' ? [rawTx]
      : shape === 'object' ? { rawTx }
        : shape === 'objectArray' ? [{ rawTx }]
          : shape === 'txObject' ? { tx: rawTx }
            : [{ tx: rawTx }];
  return rpcCall(rpcUrl, method, params, { timeoutMs, retryNetworkOnce: true });
}

async function postVerify(rpcUrl: string, methods: string[], txHash: string, timeoutMs: number): Promise<{ confirmed: boolean; status?: unknown }> {
  const checks = ['tx.getTransactionByHash', 'tx_getTransactionByHash', 'tx.getTransactionStatus', 'tx.getStatus', 'tx.status']
    .filter((method) => methods.includes(method));

  if (checks.length === 0) return { confirmed: false, status: undefined };

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
  accountSchemeId?: number;
  accountPubkeyType?: string;
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
  const chainIdActual = await getChainId(rpcUrl, methods, timeoutMs);

  // Refresh policy cache on startup/use within TTL.
  await getCapabilities(rpcUrl, chainIdActual, timeoutMs).catch(() => undefined);

  const decodeSchemeId = await detectSchemeFromDecode(rpcUrl, normalized, timeoutMs);
  const localSchemeId = input.accountSchemeId ?? extractSchemeIdFromRawTx(normalized);
  const schemeUsed = describeScheme(decodeSchemeId ?? localSchemeId);
  if (input.accountPubkeyType) {
    schemeUsed.name = `${schemeUsed.name} [${input.accountPubkeyType}]`;
  }

  const verifyPolicy = await debugVerifyForPolicy(rpcUrl, normalized, timeoutMs);
  if (verifyPolicy.blocked) {
    const refreshed = await getCapabilities(rpcUrl, chainIdActual, timeoutMs, true).catch(() => ({ allowedSchemes: [], updatedAt: Date.now() }));
    const policyError: SignaturePolicyError = {
      code: verifyPolicy.code ?? -32010,
      message: buildUserFacingPolicyMessage(schemeUsed, refreshed.allowedSchemes),
      chainIdExpected: input.chainIdExpected,
      chainIdActual,
      rpcUrl,
      schemeUsed,
      allowedSchemes: refreshed.allowedSchemes,
      action: 'SWITCH_ACCOUNT_OR_ENABLE_POLICY',
    };

    return {
      ok: false,
      error: {
        code: policyError.code,
        message: policyError.message,
        rpcUrl,
        chainIdExpected: input.chainIdExpected,
        chainIdActual,
        attempts: [],
        diagnostics: {},
        correlationId: corr,
        signaturePolicyError: policyError,
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

    if (isPolicyDisabledError(code, message)) {
      const refreshed = await getCapabilities(rpcUrl, chainIdActual, timeoutMs, true).catch(() => ({ allowedSchemes: [], updatedAt: Date.now() }));
      const policyError: SignaturePolicyError = {
        code,
        message: buildUserFacingPolicyMessage(schemeUsed, refreshed.allowedSchemes),
        chainIdExpected: input.chainIdExpected,
        chainIdActual,
        rpcUrl,
        schemeUsed,
        allowedSchemes: refreshed.allowedSchemes,
        action: 'SWITCH_ACCOUNT_OR_ENABLE_POLICY',
      };

      return {
        ok: false,
        error: {
          code,
          message: policyError.message,
          rpcUrl,
          chainIdExpected: input.chainIdExpected,
          chainIdActual,
          attempts,
          diagnostics: {},
          correlationId: corr,
          signaturePolicyError: policyError,
        },
      };
    }

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
