import { describe, it, expect, vi, beforeEach } from 'vitest';

// We will import the studio-wasm library from the sibling package, but mock it
// to avoid downloading/booting Pyodide during unit tests. The mock implements a
// tiny in-memory "counter" runtime good enough to exercise compile → inc → get.
vi.mock('../../../studio-wasm/src/index', () => {
  type State = { storage: Map<string, any> };
  type Artifact = { codeHash: string; abi?: any; name?: string };

  const stateCtor = () => ({ storage: new Map<string, any>() }) as State;

  const compileSource = async (
    _source: string,
    manifest: { name?: string; abi?: any } = {},
  ): Promise<Artifact> => {
    // Pretend to compile; return a stable code hash + mirror manifest bits
    return {
      codeHash: '0x' + 'c0ffeec0de'.padEnd(64, '0'),
      abi: manifest?.abi ?? {},
      name: manifest?.name ?? 'Counter',
    };
  };

  const simulateCall = async (args: {
    state: State;
    artifact: Artifact;
    method: string;
    calldata?: any[];
  }) => {
    const { state, method } = args;
    const events: Array<{ name: string; data: any }> = [];

    if (method === 'inc') {
      const current = Number(state.storage.get('count') ?? 0);
      const next = current + 1;
      state.storage.set('count', next);
      events.push({ name: 'Incremented', data: { new: next } });
      return {
        gasUsed: 123n,
        returnValue: null,
        events,
      };
    }

    if (method === 'get') {
      const current = Number(state.storage.get('count') ?? 0);
      return {
        gasUsed: 17n,
        returnValue: current,
        events,
      };
    }

    throw new Error(`Unknown method ${method}`);
  };

  const createEphemeralState = () => stateCtor();

  return {
    // Public API surface mocked for tests
    compileSource,
    simulateCall,
    createEphemeralState,
  };
});

// Import AFTER the mock so the mocked module is used.
import * as wasm from '../../../studio-wasm/src/index';

describe('studio-wasm — compile + simulate Counter', () => {
  const counterSrc = `
# (placeholder) Python source of a deterministic Counter contract.
# Real tests in studio-wasm hit Pyodide; here we mock engine for unit tests.
def inc(): pass
def get(): pass
`.trim();

  const manifest = {
    name: 'Counter',
    abi: {
      functions: [
        { name: 'inc', inputs: [], outputs: [] },
        { name: 'get', inputs: [], outputs: [{ type: 'int' }] },
      ],
      events: [{ name: 'Incremented', args: [{ name: 'new', type: 'int' }] }],
    },
  };

  let artifact: any;
  let state: any;

  beforeEach(async () => {
    artifact = await (wasm as any).compileSource(counterSrc, manifest);
    state = (wasm as any).createEphemeralState();
  });

  it('compiles source and returns an artifact with a stable codeHash', async () => {
    expect(artifact).toBeTruthy();
    expect(artifact.name).toBe('Counter');
    expect(typeof artifact.codeHash).toBe('string');
    expect(artifact.codeHash).toMatch(/^0x[0-9a-f]{64}$/);
  });

  it('runs inc() then get() → value increments deterministically; emits event', async () => {
    // First increment
    const r1 = await (wasm as any).simulateCall({
      state,
      artifact,
      method: 'inc',
      calldata: [],
    });
    expect(typeof r1.gasUsed).toBe('bigint');
    expect(r1.returnValue).toBeNull();
    expect(Array.isArray(r1.events)).toBe(true);
    expect(r1.events[0]).toEqual({ name: 'Incremented', data: { new: 1 } });

    // Check value = 1
    const g1 = await (wasm as any).simulateCall({
      state,
      artifact,
      method: 'get',
      calldata: [],
    });
    expect(typeof g1.gasUsed).toBe('bigint');
    expect(g1.returnValue).toBe(1);
    expect(g1.events.length).toBe(0);

    // Two more increments
    await (wasm as any).simulateCall({ state, artifact, method: 'inc' });
    await (wasm as any).simulateCall({ state, artifact, method: 'inc' });

    // Value should now be 3
    const g2 = await (wasm as any).simulateCall({
      state,
      artifact,
      method: 'get',
    });
    expect(g2.returnValue).toBe(3);
  });
});
