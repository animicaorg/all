/**
 * Canonical signing domain for animica wallet provider sign-message calls.
 *
 * All three provider RPC methods (animica_signMessage, provider_signMessage,
 * personal_sign) MUST produce signatures over the exact same byte sequence
 * for the same logical message, otherwise dapps that try methods in fallback
 * order (notably AICF) end up with mismatched verifier expectations and
 * the user sees `invalid signature` errors.
 *
 * Keep this prefix stable. Changing it is a breaking change for any
 * signed-message verifier and must be coordinated end-to-end.
 */
export const SIGN_MESSAGE_DOMAIN_PREFIX = 'animica:signMessage:';

export function buildCanonicalSignBytes(message: string): Uint8Array {
  return new TextEncoder().encode(`${SIGN_MESSAGE_DOMAIN_PREFIX}${message}`);
}
