'use strict';
/**
 * Facilitator key handling. The ONLY module that touches
 * X402_FACILITATOR_PRIVATE_KEY. Rules (spec "Wallet & key security"):
 *
 *   - key comes from the environment (populated from a 0600 env file);
 *   - malformed key => refuse to start (throw, fail closed);
 *   - the raw key never leaves this module except as the `sign` closure —
 *     it is never logged, never serialized, never returned by any API;
 *   - only the DERIVED ADDRESS is exposed for logging/metrics/supported.
 *
 * Rotation: stop the facilitator, change the env file, start. Nothing
 * user-facing references the address except /supported, which re-reads it.
 */

const evm = require('./evm');

const KEY_RE = /^(0x)?[0-9a-fA-F]{64}$/;

/**
 * Parse and validate a facilitator private key. Returns a signer:
 * { address, sign(digest) -> sigParts, signTx(txFields) -> {rawTx, hash} }.
 * The key bytes are captured in closures and deliberately not a property.
 */
function loadSigner(privateKeyHex) {
  if (typeof privateKeyHex !== 'string' || !KEY_RE.test(privateKeyHex.trim())) {
    // Do NOT echo the value back — even a malformed value may be a typo'd key.
    throw new Error('X402_FACILITATOR_PRIVATE_KEY is malformed (need 32 bytes of hex); refusing to start');
  }
  const keyBytes = evm.hexToBytes(privateKeyHex.trim());
  const keyBig = BigInt(evm.bytesToHex(keyBytes));
  if (keyBig === 0n || keyBig >= evm.SECP256K1_N) {
    throw new Error('X402_FACILITATOR_PRIVATE_KEY is out of the secp256k1 range; refusing to start');
  }
  let address;
  try {
    address = evm.privateKeyToAddress(keyBytes);
  } catch (e) {
    throw new Error('X402_FACILITATOR_PRIVATE_KEY failed address derivation; refusing to start');
  }
  return {
    address,
    sign: (digest) => evm.signDigest(digest, keyBytes),
    signTx: (txFields) => evm.signEip1559Tx(txFields, keyBytes),
    // Guard rails against accidental serialization of the signer object.
    toJSON: () => ({ address }),
    [Symbol.for('nodejs.util.inspect.custom')]: () => `[FacilitatorSigner ${address}]`,
  };
}

/**
 * Redact anything that looks like key/signature material from a value about
 * to be logged. Field-name based: values under these names are replaced.
 */
const REDACT_FIELDS = /private|secret|signature|authorization|rawtx|raw_tx|password|token_secret|api_key/i;

function redact(value, depth = 0) {
  if (depth > 6 || value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map((v) => redact(v, depth + 1));
  const out = {};
  for (const [k, v] of Object.entries(value)) {
    out[k] = REDACT_FIELDS.test(k) ? '[REDACTED]' : redact(v, depth + 1);
  }
  return out;
}

module.exports = { loadSigner, redact, REDACT_FIELDS };
