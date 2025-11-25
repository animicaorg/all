import { describe, it, expect } from 'vitest';

// We mock @animica/sdk with a minimal, deterministic implementation so this
// unit test doesn't depend on the full SDK build or live PQ libs. The mock
// focuses on deploy-tx building, sign-bytes domain separation, and a stable
// "signature" over those bytes.
vi.mock('@animica/sdk', () => {
  const { createHash, randomBytes } = require('node:crypto');

  const sha256Hex = (u8: Uint8Array | Buffer | string) =>
    createHash('sha256').update(u8).digest('hex');

  const textEnc = new TextEncoder();

  // Stable stringify with key sorting for deterministic hashing
  const stableStringify = (obj: any): string => {
    const sorter = (x: any): any => {
      if (Array.isArray(x)) return x.map(sorter);
      if (x && typeof x === 'object') {
        return Object.keys(x)
          .sort()
          .reduce((acc: any, k) => {
            acc[k] = sorter(x[k]);
            return acc;
          }, {});
      }
      return x;
    };
    return JSON.stringify(sorter(obj));
  };

  const DOMAIN = 'animica:tx:sign:v1';

  type DeployTx = {
    kind: 'deploy';
    chainId: number;
    from: string;
    nonce: bigint;
    gasLimit: bigint;
    gasPrice: bigint;
    code: Uint8Array;
    manifest: any;
  };

  const buildDeployTx = (args: {
    chainId: number;
    from: string;
    nonce: bigint | number;
    gasLimit?: bigint | number;
    gasPrice?: bigint | number;
    code: Uint8Array;
    manifest: any;
  }): DeployTx => {
    const {
      chainId,
      from,
      nonce,
      gasLimit = 1_000_000n,
      gasPrice = 1n,
      code,
      manifest,
    } = args;
    return {
      kind: 'deploy',
      chainId,
      from,
      nonce: BigInt(nonce),
      gasLimit: BigInt(gasLimit),
      gasPrice: BigInt(gasPrice),
      code,
      manifest,
    };
  };

  // Encode "sign bytes" as a JSON-then-UTF8 byte array that includes a domain
  // separator and essential fields. The real SDK uses canonical CBOR; for the
  // purpose of this test we keep things lightweight but structurally faithful.
  const encodeSignBytes = (tx: DeployTx): Uint8Array => {
    const codeHash = '0x' + sha256Hex(Buffer.from(tx.code));
    const manifestStr = stableStringify(tx.manifest ?? {});
    const manifestHash = '0x' + sha256Hex(manifestStr);
    const payload = {
      domain: DOMAIN,
      chainId: tx.chainId,
      kind: tx.kind,
      from: tx.from,
      nonce: tx.nonce.toString(), // stringify for JSON transport
      gasLimit: tx.gasLimit.toString(),
      gasPrice: tx.gasPrice.toString(),
      codeHash,
      manifestHash,
    };
    const json = stableStringify(payload);
    return textEnc.encode(json);
  };

  // Deterministic "signature": sha256 over sign-bytes plus alg_id tag, returned
  // as a hex string. Real Dilithium/SPHINCS signatures are byte strings; here
  // we just need stable behavior to assert the domain and payload are bound.
  const signDeterministic = async (signBytes: Uint8Array, _sk: Uint8Array) => {
    const h = sha256Hex(signBytes);
    const algId = 0x11; // pretend Dilithium3
    return {
      algId,
      signature: '0x' + h + h, // 64 bytes hex-like
      pubkey: '0x' + sha256Hex(Buffer.from('pk:' + h)).slice(0, 64),
    };
  };

  // Helper to decode the mock sign bytes back into an object for assertions.
  const decodeSignBytesUnsafe = (signBytes: Uint8Array) => {
    const json = new TextDecoder().decode(signBytes);
    return JSON.parse(json);
  };

  return {
    buildDeployTx,
    encodeSignBytes,
    signDeterministic,
    __internals: { decodeSignBytesUnsafe, DOMAIN },
  };
});

// Import AFTER the vi.mock so the mocked API is used.
import {
  buildDeployTx,
  encodeSignBytes,
  signDeterministic,
  // @ts-expect-error test-only internals from the mock
  __internals,
} from '@animica/sdk';

describe('deploy flow — build → encode sign bytes → sign', () => {
  const enc = new TextEncoder();

  const sampleManifest = {
    name: 'Counter',
    abi: {
      functions: [
        { name: 'inc', inputs: [], outputs: [] },
        { name: 'get', inputs: [], outputs: [{ type: 'int' }] },
      ],
      events: [{ name: 'Incremented', args: [{ name: 'new', type: 'int' }] }],
    },
    resources: { storageKeys: 4 },
  };

  const sampleCode = enc.encode(
    // Placeholder; real code hash doesn’t matter for structure tests.
    'print("counter bytecode placeholder")',
  );

  it('builds a deploy transaction with expected fields', () => {
    const tx = buildDeployTx({
      chainId: 1337,
      from: 'anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq7d0t8',
      nonce: 7n,
      gasLimit: 2_000_000n,
      gasPrice: 42n,
      code: sampleCode,
      manifest: sampleManifest,
    });

    expect(tx.kind).toBe('deploy');
    expect(tx.chainId).toBe(1337);
    expect(tx.from.startsWith('anim1')).toBe(true);
    expect(tx.nonce).toBe(7n);
    expect(tx.gasLimit).toBe(2_000_000n);
    expect(tx.gasPrice).toBe(42n);
    expect(tx.code instanceof Uint8Array).toBe(true);
    expect(typeof tx.manifest).toBe('object');
  });

  it('encodes sign-bytes with domain separation and essential fields', () => {
    const tx = buildDeployTx({
      chainId: 1337,
      from: 'anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq7d0t8',
      nonce: 7n,
      code: sampleCode,
      manifest: sampleManifest,
    });

    const signBytes = encodeSignBytes(tx);
    expect(signBytes instanceof Uint8Array).toBe(true);
    // Decode via test-only helper to assert structure
    const decoded = __internals.decodeSignBytesUnsafe(signBytes);

    expect(decoded.domain).toBe(__internals.DOMAIN);
    expect(decoded.chainId).toBe(1337);
    expect(decoded.kind).toBe('deploy');
    expect(decoded.from).toBe('anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq7d0t8');
    expect(decoded.nonce).toBe('7'); // stringified in JSON
    expect(decoded.codeHash).toMatch(/^0x[0-9a-f]{64}$/);
    expect(decoded.manifestHash).toMatch(/^0x[0-9a-f]{64}$/);
  });

  it('produces a deterministic signature over sign-bytes (binds domain & payload)', async () => {
    const tx = buildDeployTx({
      chainId: 1,
      from: 'anim1xyzxyzxyzxyzxyzxyzxyzxyzxyzxyzxyzt0k9',
      nonce: 123n,
      code: sampleCode,
      manifest: sampleManifest,
    });

    const signBytesA = encodeSignBytes(tx);
    const sigA = await signDeterministic(signBytesA, new Uint8Array([1, 2, 3]));
    const signBytesB = encodeSignBytes(tx);
    const sigB = await signDeterministic(signBytesB, new Uint8Array([9, 9, 9]));

    // Same sign-bytes → same signature, regardless of "sk" in our deterministic mock
    expect(sigA.signature).toEqual(sigB.signature);
    expect(sigA.signature).toMatch(/^0x[0-9a-f]{128}$/);

    // If any payload field changes, signature changes.
    const tx2 = { ...tx, nonce: 124n };
    const sigC = await signDeterministic(encodeSignBytes(tx2), new Uint8Array([1]));
    expect(sigC.signature).not.toEqual(sigA.signature);

    // Domain is bound — changing the domain inside sign-bytes changes signature
    const decoded = __internals.decodeSignBytesUnsafe(signBytesA);
    decoded.domain = 'wrong:domain';
    const tampered = new TextEncoder().encode(JSON.stringify(decoded));
    const sigTampered = await signDeterministic(tampered, new Uint8Array([1]));
    expect(sigTampered.signature).not.toEqual(sigA.signature);
  });
});
