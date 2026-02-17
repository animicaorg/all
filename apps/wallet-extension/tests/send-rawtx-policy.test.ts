import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('../src/services/rpcConfig', () => ({
  getEffectiveRpcUrl: (url: string) => url,
}));

const rpcCall = vi.fn();
const discoverMethods = vi.fn();
const healthProbe = vi.fn();

vi.mock('../src/services/rpcClient', () => ({
  rpcCall,
  discoverMethods,
  healthProbe,
}));

vi.mock('../src/services/diagnostics', () => ({
  runDiagnosticsBundle: vi.fn(async () => ({})),
}));

beforeEach(() => {
  vi.clearAllMocks();
  healthProbe.mockResolvedValue({ ok: true, method: 'node.ping', params: [], durationMs: 1, response: { result: 'pong' } });
  discoverMethods.mockResolvedValue(['chain.getChainId']);
});

describe('sendRawTxPipeline policy handling', () => {
  it('returns structured scheme policy error with allowed schemes', async () => {
    rpcCall.mockImplementation(async (_url: string, method: string) => {
      if (method === 'chain.getChainId') return ok(1);
      if (method === 'policy.getPqAlgPolicy') {
        return ok({ chainId: 1, allowedSchemes: [{ id: 2, name: 'SPHINCS+ SHAKE-128s', enabled: true }], defaultSchemeId: 2 });
      }
      if (method.startsWith('tx.debugVerifyRawTransaction')) {
        return err(-32601, 'Method not found');
      }
      if (method === 'tx_sendRawTransaction') {
        return err(-32010, 'Signature scheme disabled by policy');
      }
      return err(-32601, 'Method not found');
    });

    const { sendRawTxPipeline } = await import('../src/services/sendRawTx');
    const result = await sendRawTxPipeline({
      rpcUrl: 'https://rpc.test',
      rawTx: '0xa363747869645820' + '00'.repeat(32),
      accountSchemeId: 1,
      timeoutMs: 1000,
    });

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.error.signaturePolicyError).toBeTruthy();
    expect(result.error.signaturePolicyError?.action).toBe('SWITCH_ACCOUNT_OR_ENABLE_POLICY');
    expect(result.error.signaturePolicyError?.allowedSchemes).toEqual([{ id: 2, name: 'SPHINCS+ SHAKE-128s' }]);
  });

  it('does not block when allowed schemes include current scheme', async () => {
    rpcCall.mockImplementation(async (_url: string, method: string, params: unknown) => {
      if (method === 'chain.getChainId') return ok(1);
      if (method === 'policy.getPqAlgPolicy') {
        return ok({ chainId: 1, allowedSchemes: [{ id: 1, name: 'Dilithium3', enabled: true }], defaultSchemeId: 1 });
      }
      if (method.startsWith('tx.debugVerifyRawTransaction')) return err(-32601, 'Method not found');
      if (method === 'tx_sendRawTransaction') return ok('0xabc');
      if (method.startsWith('tx.getTransaction')) return ok({ status: 'pending' });
      if (Array.isArray(params) && params[0] === '0xabc') return ok({ status: 'pending' });
      return err(-32601, 'Method not found');
    });

    const { sendRawTxPipeline } = await import('../src/services/sendRawTx');
    const result = await sendRawTxPipeline({
      rpcUrl: 'https://rpc.test',
      rawTx: '0xa363747869645820' + '11'.repeat(32),
      accountSchemeId: 1,
      timeoutMs: 1000,
    });

    expect(result.ok).toBe(true);
  });

  it('uses array params first and falls back to keyword object params', async () => {
    rpcCall.mockImplementation(async (_url: string, method: string, params: unknown) => {
      if (method === 'chain.getChainId') return ok(1);
      if (method === 'policy.getPqAlgPolicy') return ok({ chainId: 1, allowedSchemes: [], defaultSchemeId: 1 });
      if (method.startsWith('tx.debugVerifyRawTransaction')) return err(-32601, 'Method not found');
      if (method === 'tx_sendRawTransaction' && Array.isArray(params) && typeof params[0] === 'string') {
        return err(-32602, 'invalid params array');
      }
      if (method === 'tx_sendRawTransaction' && !Array.isArray(params) && params && typeof params === 'object' && 'rawTx' in params) {
        return ok('0xdef');
      }
      if (method.startsWith('tx.getTransaction')) return ok({ status: 'pending' });
      return err(-32601, 'Method not found');
    });

    const { sendRawTxPipeline } = await import('../src/services/sendRawTx');
    const result = await sendRawTxPipeline({
      rpcUrl: 'https://rpc.test',
      rawTx: '0xa363747869645820' + '22'.repeat(32),
      timeoutMs: 1000,
    });

    expect(result.ok).toBe(true);
    const calls = rpcCall.mock.calls.filter((c) => c[1] === 'tx_sendRawTransaction');
    expect(calls[0][2]).toEqual([expect.any(String)]);
    expect(calls[1][2]).toEqual({ rawTx: expect.any(String) });
  });
});

function ok(result: unknown) {
  return { ok: true, method: 'x', params: [], durationMs: 1, response: { result } };
}

function err(code: number, message: string) {
  return { ok: false, method: 'x', params: [], durationMs: 1, response: { error: { code, message } } };
}
