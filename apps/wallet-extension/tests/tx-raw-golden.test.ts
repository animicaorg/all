import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { encodeTxForRpc } from '../src/core/tx/builder';
import type { SignedTx } from '../src/types/tx';

const fixtureDir = path.join(process.cwd(), 'tests', 'fixtures');
const repoRoot = path.resolve(process.cwd(), '..', '..');

function hexToBytes(hex: string): Uint8Array {
  const normalized = hex.startsWith('0x') ? hex.slice(2) : hex;
  if (normalized.length % 2 !== 0) throw new Error('Hex must have even length');
  const out = new Uint8Array(normalized.length / 2);
  for (let i = 0; i < normalized.length; i += 2) {
    out[i / 2] = parseInt(normalized.slice(i, i + 2), 16);
  }
  return out;
}

function loadFixtureSignedTx(): { signedTx: SignedTx; expectedRaw: string; expectedHash: string } {
  const all = JSON.parse(
    fs.readFileSync(path.join(fixtureDir, 'tx-golden-v2-transfer.json'), 'utf-8'),
  ) as any;

  const txBody = all.tx_body;
  const sig = all.signature;

  const signedTx: SignedTx = {
    tx: {
      v: txBody.v,
      chainId: txBody.chainId,
      from: hexToBytes(txBody.from),
      gas: txBody.gas,
      payload: {
        t: txBody.payload.t,
        v: {
          to: hexToBytes(txBody.payload.v.to),
          amount: txBody.payload.v.amount,
          data: hexToBytes(txBody.payload.v.data),
        },
      },
      accessList: txBody.accessList,
      validAfter: txBody.validAfter,
      validUntil: txBody.validUntil,
      salt: hexToBytes(txBody.salt),
    },
    sigs: [
      {
        alg: sig.alg,
        pubkey: hexToBytes(sig.pubkey),
        sig: hexToBytes(sig.sig),
      },
    ],
  };

  return {
    signedTx,
    expectedRaw: fs.readFileSync(path.join(fixtureDir, 'tx-golden-v2-transfer.rawtx.hex'), 'utf-8').trim(),
    expectedHash: fs
      .readFileSync(path.join(fixtureDir, 'tx-golden-v2-transfer.expected_tx_hash.txt'), 'utf-8')
      .trim(),
  };
}

describe('wallet extension rawTx encoding compatibility', () => {
  it('encodes tx bytes identical to node canonical CBOR fixture', () => {
    const { signedTx, expectedRaw } = loadFixtureSignedTx();
    const actualRaw = encodeTxForRpc(signedTx);
    expect(actualRaw).toBe(expectedRaw);
    expect(actualRaw.startsWith('0x')).toBe(true);
    expect((actualRaw.length - 2) % 2).toBe(0);
  });

  it('decodes in node tx decoder and keeps expected tx hash', () => {
    const { expectedRaw, expectedHash } = loadFixtureSignedTx();

    const py = `
import json, hashlib
from rpc.methods.tx import tx_decode_raw_transaction
raw = __RAW_JSON__
obj = tx_decode_raw_transaction(raw)
out = {
  'tx_hash': '0x' + hashlib.sha3_256(bytes.fromhex(raw[2:])).hexdigest(),
  'decoded_keys': sorted(list(obj.keys())),
}
print(json.dumps(out))
`;

    const rendered = py.replace('__RAW_JSON__', JSON.stringify(expectedRaw));
    const output = execFileSync('python', ['-c', rendered], {
      encoding: 'utf-8',
      cwd: repoRoot,
      env: {
        ...process.env,
        PYTHONPATH: `${repoRoot}:${path.join(repoRoot, 'python')}`,
      },
    }).trim();
    const parsed = JSON.parse(output) as { tx_hash: string; decoded_keys: string[] };

    expect(parsed.tx_hash).toBe(expectedHash);
    expect(parsed.decoded_keys).toContain('tx');
    expect(parsed.decoded_keys).toContain('type');
  });
});
