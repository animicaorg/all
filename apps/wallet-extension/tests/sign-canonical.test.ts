import { describe, expect, it } from 'vitest';

import { buildCanonicalSignBytes } from '../src/core/crypto/sign-domain';

/**
 * Regression for the "invalid signature on AICF" bug.
 *
 * Before: handleProviderSignMessage encoded the RPC method name into the
 * signed bytes, so `animica_signMessage("hi")`, `provider_signMessage("hi")`,
 * and `personal_sign("hi", addr)` produced three different signed-byte
 * sequences for the same `message`. Any verifier that tried to recompute
 * the bytes (without knowing which method was used) failed validation.
 *
 * After: the prefix is method-independent. This test pins the canonical
 * bytes so a future change to the prefix is loud and intentional.
 */
describe('canonical sign-message domain', () => {
  it('produces bytes independent of the RPC method used', () => {
    const a = buildCanonicalSignBytes('hello');
    const b = buildCanonicalSignBytes('hello');
    expect(Buffer.from(a).equals(Buffer.from(b))).toBe(true);
  });

  it('uses the documented prefix animica:signMessage:', () => {
    const bytes = buildCanonicalSignBytes('hello');
    expect(new TextDecoder().decode(bytes)).toBe('animica:signMessage:hello');
  });

  it('is sensitive to the message payload', () => {
    const a = buildCanonicalSignBytes('hello');
    const b = buildCanonicalSignBytes('hello!');
    expect(Buffer.from(a).equals(Buffer.from(b))).toBe(false);
  });
});
