import { describe, it, expect } from 'vitest';

// We mock the studio-services client used by studio-web. The mock re-compiles
// (hashes) the provided source+manifest and compares it against a configurable
// "on-chain" expected hash to produce a verification result. We simulate a
// short-lived queue by returning {status:"pending"} once before "complete".
vi.mock('../../src/services/servicesApi', () => {
  const { createHash } = require('node:crypto');

  const sha256Hex = (u8: Uint8Array | Buffer | string) =>
    createHash('sha256').update(u8).digest('hex');

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

  const computeCodeHash = (source: string, manifest: any): string => {
    const body = Buffer.concat([
      Buffer.from(source, 'utf8'),
      Buffer.from('\n', 'utf8'),
      Buffer.from(stableStringify(manifest), 'utf8'),
    ]);
    return '0x' + sha256Hex(body);
  };

  let ON_CHAIN_HASH = '0x' + '00'.repeat(32);
  const __mockSetOnChainHash = (h: string) => {
    ON_CHAIN_HASH = h;
  };

  type Job = {
    polls: number;
    result: {
      address?: string;
      txHash?: string;
      expectedHash: string;
      codeHash: string;
      match: boolean;
      details?: Record<string, unknown>;
    };
  };

  const jobs = new Map<string, Job>();

  const verifySubmit = async (req: {
    address?: string;
    txHash?: string;
    source: string;
    manifest: any;
  }): Promise<{ jobId: string }> => {
    const jobId = 'job_' + Math.random().toString(36).slice(2);
    const codeHash = computeCodeHash(req.source, req.manifest);
    const match = codeHash === ON_CHAIN_HASH;
    jobs.set(jobId, {
      polls: 0,
      result: {
        address: req.address,
        txHash: req.txHash,
        expectedHash: ON_CHAIN_HASH,
        codeHash,
        match,
        details: { manifestSize: Buffer.byteLength(stableStringify(req.manifest), 'utf8') },
      },
    });
    return { jobId };
  };

  const getVerify = async (
    jobId: string,
  ): Promise<
    | { status: 'pending' }
    | { status: 'complete'; result: Job['result'] }
  > => {
    const j = jobs.get(jobId);
    if (!j) throw new Error('verify job not found');
    j.polls++;
    if (j.polls < 2) return { status: 'pending' };
    return { status: 'complete', result: j.result };
  };

  return {
    verifySubmit,
    getVerify,
    __mockSetOnChainHash,
    // Types exported in real module (kept minimal for consumer compatibility):
    // export type VerifyRequest/Response ... not required by this test
  };
});

// Import AFTER the mock so we get the mocked API.
import {
  verifySubmit,
  getVerify,
  // @ts-expect-error test-only helper provided by the mock
  __mockSetOnChainHash,
} from '../../src/services/servicesApi';

describe('verify flow — recompile & code-hash match (services mock)', () => {
  const manifest = {
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

  const goodSource = `# counter
def inc(state):
    cur = state.get('v') or 0
    state.set('v', cur + 1)

def get(state):
    return state.get('v') or 0
`;

  const badSource = goodSource + '\n# tampered\n';

  // Helper to reproduce the mock's hashing logic locally, so we can seed the
  // expected "on-chain" hash for the positive test case.
  const localHash = (source: string, manifestObj: any) => {
    const { createHash } = require('node:crypto');
    const stable = (obj: any): string => {
      const s = (x: any): any => {
        if (Array.isArray(x)) return x.map(s);
        if (x && typeof x === 'object') {
          return Object.keys(x)
            .sort()
            .reduce((acc: any, k) => {
              acc[k] = s(x[k]);
              return acc;
            }, {});
        }
        return x;
      };
      return JSON.stringify(s(obj));
    };
    const buf = Buffer.concat([
      Buffer.from(source, 'utf8'),
      Buffer.from('\n', 'utf8'),
      Buffer.from(stable(manifestObj), 'utf8'),
    ]);
    return '0x' + createHash('sha256').update(buf).digest('hex');
  };

  it('returns match=true when recompiled hash equals on-chain expected', async () => {
    const expected = localHash(goodSource, manifest);
    __mockSetOnChainHash(expected);

    const { jobId } = await verifySubmit({
      address: 'anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq7d0t8',
      source: goodSource,
      manifest,
    });

    // First poll → pending
    const pending = await getVerify(jobId);
    expect(pending.status).toBe('pending');

    // Second poll → complete with result
    const done = await getVerify(jobId);
    expect(done.status).toBe('complete');
    expect(done.result.match).toBe(true);
    expect(done.result.expectedHash).toBe(expected);
    expect(done.result.codeHash).toBe(expected);
  });

  it('returns match=false when source differs from deployed artifact', async () => {
    const expected = localHash(goodSource, manifest); // on-chain reflects GOOD code
    __mockSetOnChainHash(expected);

    const { jobId } = await verifySubmit({
      address: 'anim1xyzxyzxyzxyzxyzxyzxyzxyzxyzxyzxyzt0k9',
      source: badSource, // tampered
      manifest,
    });

    // Simulated queue: pending then complete
    await getVerify(jobId); // pending
    const done = await getVerify(jobId); // complete

    expect(done.status).toBe('complete');
    expect(done.result.match).toBe(false);
    expect(done.result.expectedHash).toBe(expected);
    expect(done.result.codeHash).not.toBe(expected);
    expect(done.result.codeHash).toMatch(/^0x[0-9a-f]{64}$/);
  });
});
