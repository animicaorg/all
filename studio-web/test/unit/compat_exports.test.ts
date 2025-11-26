import { describe, it, expect } from 'vitest';
import { shortenHex } from '../../src/utils/format';
import { bytesFromHex, hexFromBytes } from '../../src/utils/bytes';
import { safeJsonParse } from '../../src/utils/schema';
import { downloadJson } from '../../src/utils/download';
import { sha3_512_hex } from '../../src/utils/hash';
import { loadTemplateById, ensureDefaultTemplate } from '../../src/services/templates';
import { useNetwork, useNetworkState } from '../../src/state/network';
import { useAccount, useAccountState } from '../../src/state/account';
import { useToasts, useToastState } from '../../src/state/toasts';
import { useProjectStore } from '../../src/state/project';
import { useCompileStore } from '../../src/state/compile';
import { useSimulateStore } from '../../src/state/simulate';

// Compatibility and export surface sanity checks

describe('compatibility exports', () => {
  it('provides format/bytes/schema/download/hash helpers', () => {
    expect(shortenHex('0x123456')).toBe('0x123456');
    expect(bytesFromHex('0x01')).toBeInstanceOf(Uint8Array);
    expect(hexFromBytes(new Uint8Array([0x01]))).toBe('0x01');
    expect(safeJsonParse('{"ok":true}', { ok: false })).toEqual({ ok: true });
    expect(safeJsonParse('{oops', { ok: false })).toEqual({ ok: false });
    expect(typeof downloadJson).toBe('function');
    expect(typeof sha3_512_hex('test')).toBe('string');
  });

  it('exposes template helpers', () => {
    expect(typeof loadTemplateById).toBe('function');
    expect(typeof ensureDefaultTemplate).toBe('function');
  });

  it('exposes state hooks', () => {
    expect(typeof useNetwork).toBe('function');
    expect(typeof useNetworkState).toBe('function');
    expect(typeof useAccount).toBe('function');
    expect(typeof useAccountState).toBe('function');
    expect(typeof useToasts).toBe('function');
    expect(typeof useToastState).toBe('function');
    expect(typeof useProjectStore).toBe('function');
    expect(typeof useCompileStore).toBe('function');
    expect(typeof useSimulateStore).toBe('function');
  });
});
