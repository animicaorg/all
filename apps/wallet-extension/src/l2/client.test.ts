import { describe, expect, it, vi } from 'vitest';
import { L2Client } from './client';
import type { L2Signer, L2PreparedTransfer, L2TransactionInfo } from './client';
import { bytesToHex, hexToBytes } from '../core/crypto/convert';

// A deterministic 64-byte sha3-512-shaped signing hash (0x + 128 hex chars).
const SIGNING_HASH = '0x' + '11'.repeat(64);
const BODY_HEX = '0xabcdef0123456789';
// ML-DSA-65 sizes so the fixtures look like real key material.
const PUBKEY = new Uint8Array(1952).fill(7);
const SIGNATURE = new Uint8Array(3309).fill(9);

function makePrepared(overrides: Partial<L2PreparedTransfer> = {}): L2PreparedTransfer {
  return {
    kind: 'transfer',
    sender: 'anim1sender',
    recipient: 'anim1recipient',
    amount: '1500000000',
    nonce: 4,
    fee: '1000000',
    requiredFee: '1000000',
    l2ChainId: 4242,
    bodyHex: BODY_HEX,
    signingHash: SIGNING_HASH,
    sigScheme: 'ml_dsa_65',
    ...overrides,
  };
}

/**
 * Build a mock L2 transport that answers l2_prepareTransfer / l2_submitSigned
 * / l2_getTransaction and records every call so we can assert request shaping.
 */
function makeMockTransport(opts: {
  prepared?: L2PreparedTransfer;
  txid?: string;
  status?: L2TransactionInfo['status'];
} = {}) {
  const prepared = opts.prepared ?? makePrepared();
  const txid = opts.txid ?? '0xdeadbeef';
  const status = opts.status ?? 'PROVEN';
  const calls: Array<{ method: string; params: any }> = [];

  const call = vi.fn(async (method: string, params?: any) => {
    calls.push({ method, params });
    switch (method) {
      case 'l2_prepareTransfer':
        return prepared;
      case 'l2_submitSigned':
        return txid;
      case 'l2_getTransaction':
        return { txid, status, batch: 1, receipt: {}, reason: null } as L2TransactionInfo;
      default:
        throw new Error(`unexpected method ${method}`);
    }
  });

  return { transport: { call }, calls, call, prepared, txid, status };
}

function makeRecordingSigner() {
  const seen: Uint8Array[] = [];
  const signer: L2Signer = {
    address: 'anim1sender',
    publicKey: PUBKEY,
    algId: 0x1003,
    signHash: vi.fn(async (hash: Uint8Array) => {
      // Capture a copy so later mutation can't rewrite history.
      seen.push(Uint8Array.from(hash));
      return SIGNATURE;
    }),
  };
  return { signer, seen };
}

describe('L2Client signing recipe', () => {
  it('signs the prepared signingHash directly and submits pubkey + signature', async () => {
    const mock = makeMockTransport();
    const { signer, seen } = makeRecordingSigner();
    const client = new L2Client(mock.transport);

    const result = await client.sendInstant(
      { to: 'anim1recipient', amount: 1_500_000_000n, memo: 'hi' },
      signer,
      { sleep: async () => {} },
    );

    // 1. l2_prepareTransfer shaped from the intent.
    const prepareCall = mock.calls.find((c) => c.method === 'l2_prepareTransfer');
    expect(prepareCall).toBeDefined();
    expect(prepareCall!.params).toMatchObject({
      kind: 'transfer',
      sender: 'anim1sender',
      recipient: 'anim1recipient',
      amount: '1500000000',
      memo: 'hi',
    });

    // 2. The signer received EXACTLY the 64 raw bytes of signingHash — no rehash.
    expect(seen).toHaveLength(1);
    expect(bytesToHex(seen[0])).toBe(SIGNING_HASH);
    expect(seen[0]).toEqual(hexToBytes(SIGNING_HASH, 'signingHash'));

    // 3. l2_submitSigned called with body + 0x-hex pubkey + 0x-hex signature.
    const submitCall = mock.calls.find((c) => c.method === 'l2_submitSigned');
    expect(submitCall).toBeDefined();
    expect(submitCall!.params).toEqual({
      body: BODY_HEX,
      pubkey: bytesToHex(PUBKEY, 'pubkey'),
      signature: bytesToHex(SIGNATURE, 'signature'),
    });

    // 4. Polled to PROVEN and reported settlement truthfully.
    expect(result.txid).toBe('0xdeadbeef');
    expect(result.status).toBe('PROVEN');
    expect(result.proven).toBe(true);
  });

  it('shapes withdrawToL1 with kind="withdraw"', async () => {
    const mock = makeMockTransport({ prepared: makePrepared({ kind: 'withdraw' }) });
    const { signer } = makeRecordingSigner();
    const client = new L2Client(mock.transport);

    await client.withdrawToL1(
      { to: 'anim1recipient', amount: '2000000000' },
      signer,
      { sleep: async () => {} },
    );

    const prepareCall = mock.calls.find((c) => c.method === 'l2_prepareTransfer');
    expect(prepareCall!.params.kind).toBe('withdraw');
    expect(prepareCall!.params.amount).toBe('2000000000');
  });

  it('does not report SOFT_CONFIRMED as proven', async () => {
    const mock = makeMockTransport({ status: 'SOFT_CONFIRMED' });
    const { signer } = makeRecordingSigner();
    const client = new L2Client(mock.transport);

    const result = await client.sendInstant(
      { to: 'anim1recipient', amount: 1n },
      signer,
      { sleep: async () => {}, timeoutMs: 1, intervalMs: 1 },
    );

    expect(result.status).toBe('SOFT_CONFIRMED');
    expect(result.proven).toBe(false);
  });

  it('throws on a REVERTED transaction', async () => {
    const mock = makeMockTransport({ status: 'REVERTED' });
    const { signer } = makeRecordingSigner();
    const client = new L2Client(mock.transport);

    await expect(
      client.sendInstant({ to: 'anim1recipient', amount: 1n }, signer, { sleep: async () => {} }),
    ).rejects.toThrow(/REVERTED/);
  });

  it('sends l2_getBalance with a named address param', async () => {
    const call = vi.fn(async () => ({
      address: 'anim1x',
      balance: '5000000000',
      nonce: 2,
      pendingNonce: 2,
      unit: 'nanoANM',
    }));
    const client = new L2Client({ call });
    const bal = await client.l2GetBalance('anim1x');
    expect(call).toHaveBeenCalledWith('l2_getBalance', { address: 'anim1x' });
    expect(bal.balance).toBe('5000000000');
  });
});
