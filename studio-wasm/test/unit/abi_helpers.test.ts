import { describe, it, expect } from 'vitest';

/**
 * This test exercises the Python-side ABI helpers exposed via Pyodide.
 * We encode/decode a few scalar types and ensure round-trips match.
 *
 * If Pyodide assets are not available in the current environment, the test
 * will gracefully skip (similar to other Pyodide-dependent tests).
 */

describe('abi_helpers encode/decode', () => {
  it('round-trips int, bool, and bytes', async () => {
    const { getPyodide } = await import('/src/pyodide/loader');

    let pyodide: any;
    try {
      pyodide = await getPyodide();
    } catch (err: any) {
      // eslint-disable-next-line no-console
      console.warn('[abi_helpers.test] skipping: could not boot Pyodide:', err?.message || err);
      expect(true).toBe(true);
      return;
    }

    const py = String.raw;
    const result = pyodide.runPython<any>(py`
import binascii
from bridge import abi_helpers as ah

def hx(b: bytes) -> str:
    return binascii.hexlify(b).decode('ascii')

out = {}

# int
enc_i = ah.encode_scalar(42, 'int')
dec_i = ah.decode_scalar(enc_i, 'int')
out['int_enc_hex'] = hx(enc_i)
out['int_dec'] = dec_i

# bool
enc_b = ah.encode_scalar(True, 'bool')
dec_b = ah.decode_scalar(enc_b, 'bool')
out['bool_enc_hex'] = hx(enc_b)
out['bool_dec'] = dec_b

# bytes
val_bytes = bytes.fromhex('00ff10deadbeef')
enc_y = ah.encode_scalar(val_bytes, 'bytes')
dec_y = ah.decode_scalar(enc_y, 'bytes')
out['bytes_enc_hex'] = hx(enc_y)
out['bytes_dec_hex'] = hx(dec_y)

out
    `);

    // Convert PyProxy dict → plain JS object if needed
    const obj: any = typeof result?.toJs === 'function' ? result.toJs({ dict_converter: Object.fromEntries }) : result;

    // Basic shape
    expect(obj).toBeTruthy();
    expect(typeof obj.int_enc_hex).toBe('string');
    expect(typeof obj.bool_enc_hex).toBe('string');
    expect(typeof obj.bytes_enc_hex).toBe('string');

    // Round-trip equality
    expect(obj.int_dec).toBe(42);
    expect(obj.bool_dec).toBe(true);
    expect(obj.bytes_dec_hex).toBe('00ff10deadbeef');

    // Encodings should be non-empty (exact encoding is implementation-defined)
    expect(obj.int_enc_hex.length).toBeGreaterThan(0);
    expect(obj.bool_enc_hex.length).toBeGreaterThan(0);
    expect(obj.bytes_enc_hex.length).toBeGreaterThan(0);
  }, 120_000);
});
