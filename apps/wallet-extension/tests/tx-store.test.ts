import { describe, it, expect } from 'vitest';
import { TxStore } from '../src/core/tx/store';
import { TxStatus } from '../src/types/tx';
import type { PendingTx, SignedTx } from '../src/types/tx';
import { addressFromPubkey, decodeAddress } from '../src/core/crypto/address';

describe('Transaction Store Idempotency', () => {
  it('should not create duplicate transactions', () => {
    const store = new TxStore();

    const mockSignedTx: SignedTx = {
      tx: {
        v: 2,
        chainId: 1,
        from: new Uint8Array(32),
        gas: { price: 1000, limit: 21000 },
        payload: { t: 0, v: { to: new Uint8Array(32), amount: 1000 } },
        validAfter: 100,
        validUntil: 220,
        salt: new Uint8Array(32),
      },
      sigs: [],
    };

    const tx: PendingTx = {
      txid: 'test-txid-123',
      unsignedHash: 'unsigned-hash',
      signedTx: mockSignedTx,
      status: TxStatus.SUBMITTED,
      submittedAt: Date.now(),
    };

    store.upsert(tx);
    store.upsert(tx);

    const all = store.getAll();
    expect(all).toHaveLength(1);
  });

  it('should update status only if later in lifecycle', () => {
    const store = new TxStore();

    const mockSignedTx: SignedTx = {
      tx: {
        v: 2,
        chainId: 1,
        from: new Uint8Array(32),
        gas: { price: 1000, limit: 21000 },
        payload: { t: 0, v: { to: new Uint8Array(32), amount: 1000 } },
        validAfter: 100,
        validUntil: 220,
        salt: new Uint8Array(32),
      },
      sigs: [],
    };

    const tx1: PendingTx = {
      txid: 'test-txid',
      unsignedHash: 'unsigned-hash',
      signedTx: mockSignedTx,
      status: TxStatus.SUBMITTED,
      submittedAt: Date.now(),
    };

    store.upsert(tx1);

    const tx2: PendingTx = {
      ...tx1,
      status: TxStatus.MEMPOOL_ACCEPTED,
    };

    store.upsert(tx2);

    const stored = store.get('test-txid');
    expect(stored?.status).toBe(TxStatus.MEMPOOL_ACCEPTED);

    // Try to downgrade status
    const tx3: PendingTx = {
      ...tx1,
      status: TxStatus.CREATED_LOCAL,
    };

    store.upsert(tx3);

    const stillStored = store.get('test-txid');
    expect(stillStored?.status).toBe(TxStatus.MEMPOOL_ACCEPTED);
  });

  it('should calculate pending outgoing correctly', () => {
    const store = new TxStore();
    const senderAddress = addressFromPubkey(new Uint8Array(1952).fill(7), 0x1001);
    const senderDigest = decodeAddress(senderAddress).digest;
    const otherAddress = addressFromPubkey(new Uint8Array(1952).fill(8), 0x1001);
    const otherDigest = decodeAddress(otherAddress).digest;

    const mockSignedTx = (amount: number, from: Uint8Array): SignedTx => ({
      tx: {
        v: 2,
        chainId: 1,
        from,
        gas: { price: 1000, limit: 21000 },
        payload: { t: 0, v: { to: new Uint8Array(32), amount } },
        validAfter: 100,
        validUntil: 220,
        salt: new Uint8Array(32),
      },
      sigs: [],
    });

    store.upsert({
      txid: 'tx1',
      unsignedHash: 'hash1',
      signedTx: mockSignedTx(1000, senderDigest),
      status: TxStatus.SUBMITTED,
      submittedAt: Date.now(),
    });

    store.upsert({
      txid: 'tx2',
      unsignedHash: 'hash2',
      signedTx: mockSignedTx(2000, senderDigest),
      status: TxStatus.MEMPOOL_ACCEPTED,
      submittedAt: Date.now(),
    });

    store.upsert({
      txid: 'tx3',
      unsignedHash: 'hash3',
      signedTx: mockSignedTx(3000, senderDigest),
      status: TxStatus.CONFIRMED,
      submittedAt: Date.now(),
    });

    store.upsert({
      txid: 'tx4',
      unsignedHash: 'hash4',
      signedTx: mockSignedTx(4000, otherDigest),
      status: TxStatus.SUBMITTED,
      submittedAt: Date.now(),
    });

    const pending = store.getPendingOutgoing(senderAddress);
    expect(pending).toBe(BigInt(3000)); // Only active txs (tx1 + tx2)
  });

  it('should count pending outgoing for canonical tx body shape', () => {
    const store = new TxStore();
    const senderAddress = addressFromPubkey(new Uint8Array(1952).fill(11), 0x1001);
    const senderDigest = decodeAddress(senderAddress).digest;

    store.upsert({
      txid: 'tx-canonical',
      unsignedHash: 'hash-canonical',
      signedTx: {
        tx: {
          version: 1,
          chain_id: 1,
          nonce: 7,
          from_addr: senderDigest,
          to_addr: new Uint8Array(32),
          value: '5000',
          fee: '1000',
          gas_limit: '21000',
          data: new Uint8Array(),
          memo: '',
          timestamp: 123456,
          kind: 0,
        } as any,
        sigs: [],
      },
      status: TxStatus.SUBMITTED,
      submittedAt: Date.now(),
    });

    const pending = store.getPendingOutgoing(senderAddress);
    expect(pending).toBe(5000n);
  });

  it('should serialize and deserialize correctly', () => {
    const store = new TxStore();

    const mockSignedTx: SignedTx = {
      tx: {
        v: 2,
        chainId: 1,
        from: new Uint8Array(32),
        gas: { price: 1000, limit: 21000 },
        payload: { t: 0, v: { to: new Uint8Array(32), amount: 5000 } },
        validAfter: 100,
        validUntil: 220,
        salt: new Uint8Array(32),
      },
      sigs: [],
    };

    store.upsert({
      txid: 'test-tx',
      unsignedHash: 'hash',
      signedTx: mockSignedTx,
      status: TxStatus.SUBMITTED,
      submittedAt: 12345,
    });

    const json = store.toJSON();
    const restored = TxStore.fromJSON(json);

    expect(restored.getAll()).toHaveLength(1);
    expect(restored.get('test-tx')?.status).toBe(TxStatus.SUBMITTED);
  });
});
