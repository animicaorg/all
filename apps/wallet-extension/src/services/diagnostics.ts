import { discoverMethods, rpcCall } from './rpcClient';

export type DiagnosticRecord = {
  ok: boolean;
  method: string;
  paramsShape: 'object' | 'objectArray' | 'array' | 'none';
  response?: unknown;
  error?: { code: number | 'RPC_ERROR_UNKNOWN'; message: string; data?: unknown };
};

export type DiagnosticsBundle = {
  discover?: { methods?: string[]; error?: string };
  chainId?: DiagnosticRecord[];
  decode?: DiagnosticRecord[];
  verify?: DiagnosticRecord[];
  explainReject?: DiagnosticRecord[];
  mempoolSim?: DiagnosticRecord[];
  status?: DiagnosticRecord[];
  nonce?: DiagnosticRecord[];
  chainIdentity?: DiagnosticRecord[];
};

function errCode(code?: number): number | 'RPC_ERROR_UNKNOWN' {
  return typeof code === 'number' ? code : 'RPC_ERROR_UNKNOWN';
}

async function callShapes(rpcUrl: string, method: string, rawTx: string, timeoutMs: number): Promise<DiagnosticRecord[]> {
  const out: DiagnosticRecord[] = [];
  const variants: Array<{ params: unknown; paramsShape: DiagnosticRecord['paramsShape'] }> = [
    { params: [rawTx], paramsShape: 'array' },
    { params: { rawTx }, paramsShape: 'object' },
    { params: [{ rawTx }], paramsShape: 'objectArray' },
  ];

  for (const variant of variants) {
    const result = await rpcCall(rpcUrl, method, variant.params, { timeoutMs, retryNetworkOnce: true });
    if (result.ok) {
      out.push({ ok: true, method, paramsShape: variant.paramsShape, response: result.response?.result });
      break;
    }
    out.push({
      ok: false,
      method,
      paramsShape: variant.paramsShape,
      error: {
        code: errCode(result.response?.error?.code),
        message: result.response?.error?.message ?? result.networkError ?? result.protocolError ?? 'RPC call failed',
        data: result.response?.error?.data,
      },
      response: result.response,
    });
    if (result.response?.error?.code !== -32602 && result.response?.error?.code !== -32601) break;
  }
  return out;
}

export async function runDiagnosticsBundle(input: {
  rpcUrl: string;
  rawTx: string;
  txHash?: string;
  fromAddress?: string;
  timeoutMs?: number;
}): Promise<DiagnosticsBundle> {
  const timeoutMs = input.timeoutMs ?? 20_000;
  const methods = await discoverMethods(input.rpcUrl, timeoutMs).catch((error) => {
    return Promise.reject(new Error(error instanceof Error ? error.message : String(error)));
  });

  const bundle: DiagnosticsBundle = {
    discover: { methods },
  };

  const hasMethod = (name: string): boolean => methods.includes(name);

  const chainIdMethods = ['chain.getChainId', 'chain_getChainId'].filter(hasMethod);
  if (chainIdMethods.length > 0) {
    bundle.chainId = [];
    for (const method of chainIdMethods) {
      const out = await rpcCall(input.rpcUrl, method, [], { timeoutMs, retryNetworkOnce: true });
      bundle.chainId.push(out.ok
        ? { ok: true, method, paramsShape: 'none', response: out.response?.result }
        : {
            ok: false,
            method,
            paramsShape: 'none',
            error: { code: errCode(out.response?.error?.code), message: out.response?.error?.message ?? out.networkError ?? out.protocolError ?? 'RPC call failed', data: out.response?.error?.data },
            response: out.response,
          });
    }
  }

  if (hasMethod('chain.getChainIdentity')) {
    const out = await rpcCall(input.rpcUrl, 'chain.getChainIdentity', [], { timeoutMs, retryNetworkOnce: true });
    bundle.chainIdentity = [out.ok
      ? { ok: true, method: 'chain.getChainIdentity', paramsShape: 'none', response: out.response?.result }
      : { ok: false, method: 'chain.getChainIdentity', paramsShape: 'none', error: { code: errCode(out.response?.error?.code), message: out.response?.error?.message ?? out.networkError ?? out.protocolError ?? 'RPC call failed', data: out.response?.error?.data }, response: out.response }];
  }

  if (input.fromAddress && hasMethod('state.getPendingNonce')) {
    const out = await rpcCall(input.rpcUrl, 'state.getPendingNonce', [input.fromAddress], { timeoutMs, retryNetworkOnce: true });
    bundle.nonce = [out.ok
      ? { ok: true, method: 'state.getPendingNonce', paramsShape: 'array', response: out.response?.result }
      : { ok: false, method: 'state.getPendingNonce', paramsShape: 'array', error: { code: errCode(out.response?.error?.code), message: out.response?.error?.message ?? out.networkError ?? out.protocolError ?? 'RPC call failed', data: out.response?.error?.data }, response: out.response }];
  }

  const verifyCandidates = ['tx.debugVerifyRawTransaction', 'tx_debugVerifyRawTransaction'].filter(hasMethod);
  if (verifyCandidates.length > 0) {
    bundle.verify = [];
    for (const method of verifyCandidates) bundle.verify.push(...(await callShapes(input.rpcUrl, method, input.rawTx, timeoutMs)));
  }

  const decodeCandidates = ['tx.decodeRawTransaction', 'tx_decodeRawTransaction'].filter(hasMethod);
  if (decodeCandidates.length > 0) {
    bundle.decode = [];
    for (const method of decodeCandidates) bundle.decode.push(...(await callShapes(input.rpcUrl, method, input.rawTx, timeoutMs)));
  }

  const explainCandidates = ['tx.explainReject', 'tx.explain_reject', 'debug.explainReject'].filter(hasMethod);
  if (explainCandidates.length > 0) {
    bundle.explainReject = [];
    for (const method of explainCandidates) bundle.explainReject.push(...(await callShapes(input.rpcUrl, method, input.rawTx, timeoutMs)));
  }

  if (hasMethod('mempool.simulateAdmission')) {
    bundle.mempoolSim = await callShapes(input.rpcUrl, 'mempool.simulateAdmission', input.rawTx, timeoutMs);
  }

  if (input.txHash) {
    const statusMethods = ['debug.txStatus', 'debug_tx_status'].filter(hasMethod);
    if (statusMethods.length > 0) {
      bundle.status = [];
      for (const method of statusMethods) {
        const out = await rpcCall(input.rpcUrl, method, [input.txHash], { timeoutMs, retryNetworkOnce: true });
        bundle.status.push(out.ok
          ? { ok: true, method, paramsShape: 'array', response: out.response?.result }
          : { ok: false, method, paramsShape: 'array', error: { code: errCode(out.response?.error?.code), message: out.response?.error?.message ?? out.networkError ?? out.protocolError ?? 'RPC call failed', data: out.response?.error?.data }, response: out.response });
      }
    }
  }

  return bundle;
}
