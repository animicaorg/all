'use strict';
/**
 * Stateless verification of an exact-EVM (eip3009) payment payload against
 * this facilitator's OWN configuration. Spec §7: /verify is read-only — it
 * never writes chain state and never commits payment state; the only DB
 * access is a read (a consumed authorization is invalid whether the chain
 * or our ledger says so).
 *
 * Check order mirrors the scheme spec (scheme_exact_evm.md):
 *   structure -> requirements-vs-config -> recipient -> amount -> validity
 *   window -> signature recovery -> payer balance -> nonce unused (chain +
 *   DB). Reason strings are the spec's exact error codes.
 */

const evm = require('./evm');
const usdc = require('./usdc');

class VerifyReject extends Error {
  constructor(reason, detail) {
    super(detail || reason);
    this.reason = reason;
  }
}

const NONCE_RE = /^0x[0-9a-fA-F]{64}$/;

function toBigIntField(v, name, reason) {
  if (typeof v === 'string' && /^\d+$/.test(v)) return BigInt(v);
  if (typeof v === 'number' && Number.isSafeInteger(v) && v >= 0) return BigInt(v);
  throw new VerifyReject(reason, `${name} must be a non-negative decimal string`);
}

/**
 * Structural + config validation, no I/O. Returns normalized facts:
 * { requirements, authorization: {from,to,value,validAfter,validBefore,nonce},
 *   signature, digest, digestHex, payer }.
 */
function checkPayload(body, cfg, domainSepBytes) {
  if (!body || typeof body !== 'object') throw new VerifyReject('invalid_payload', 'body must be a JSON object');
  if (body.x402Version !== 2) throw new VerifyReject('invalid_x402_version', 'x402Version must be 2');
  const req = body.paymentRequirements;
  const pay = body.paymentPayload;
  if (!req || typeof req !== 'object' || !pay || typeof pay !== 'object') {
    throw new VerifyReject('invalid_payload', 'paymentPayload and paymentRequirements are required');
  }
  if (req.scheme !== 'exact') throw new VerifyReject('unsupported_scheme', 'only the exact scheme is supported');
  if (req.network !== cfg.caip2) {
    throw new VerifyReject('invalid_network', `this facilitator serves ${cfg.caip2}`);
  }
  // Requirements must agree with OUR config — the payTo/asset a gateway may
  // advertise never overrides the facilitator's own allowlist (defense in
  // depth against a misconfigured or compromised gateway).
  if (typeof req.asset !== 'string' || !evm.addressEquals(req.asset, cfg.asset)) {
    throw new VerifyReject('invalid_payment_requirements', `asset must be ${cfg.asset}`);
  }
  if (typeof req.payTo !== 'string' || !evm.addressEquals(req.payTo, cfg.settlementAddress)) {
    throw new VerifyReject('invalid_payment_requirements', 'payTo does not match this facilitator\'s settlement address');
  }
  if (typeof req.amount !== 'string' || !/^\d+$/.test(req.amount) || BigInt(req.amount) <= 0n) {
    throw new VerifyReject('invalid_payment_requirements', 'amount must be a positive atomic-unit integer string');
  }
  if (req.extra) {
    if (req.extra.name !== undefined && req.extra.name !== cfg.eip712.name) {
      throw new VerifyReject('invalid_payment_requirements', `extra.name must be ${JSON.stringify(cfg.eip712.name)} on ${cfg.network}`);
    }
    if (req.extra.assetTransferMethod !== undefined && req.extra.assetTransferMethod !== 'eip3009') {
      throw new VerifyReject('invalid_payment_requirements', 'only assetTransferMethod eip3009 is supported');
    }
  }

  const p = pay.payload;
  if (!p || typeof p !== 'object') throw new VerifyReject('invalid_payload', 'paymentPayload.payload is required');
  const auth = p.authorization;
  if (!auth || typeof auth !== 'object') throw new VerifyReject('invalid_payload', 'payload.authorization is required');
  if (typeof p.signature !== 'string' || !evm.isHex(p.signature, 65)) {
    throw new VerifyReject('invalid_exact_evm_payload_signature', 'payload.signature must be 65 bytes of hex');
  }
  if (!evm.isAddress(auth.from)) throw new VerifyReject('invalid_payload', 'authorization.from must be an EVM address');
  if (!evm.isAddress(auth.to)) throw new VerifyReject('invalid_payload', 'authorization.to must be an EVM address');
  if (typeof auth.nonce !== 'string' || !NONCE_RE.test(auth.nonce)) {
    throw new VerifyReject('invalid_payload', 'authorization.nonce must be 32 bytes of 0x hex');
  }
  const value = toBigIntField(auth.value, 'authorization.value', 'invalid_payload');
  const validAfter = toBigIntField(auth.validAfter, 'authorization.validAfter', 'invalid_payload');
  const validBefore = toBigIntField(auth.validBefore, 'authorization.validBefore', 'invalid_payload');

  if (!evm.addressEquals(auth.to, cfg.settlementAddress)) {
    throw new VerifyReject('invalid_exact_evm_payload_recipient_mismatch', 'authorization.to must equal payTo');
  }
  if (value !== BigInt(req.amount)) {
    throw new VerifyReject(
      'invalid_exact_evm_payload_authorization_value_mismatch',
      `authorization.value ${value} != required amount ${req.amount}`
    );
  }

  const normalized = {
    from: auth.from,
    to: auth.to,
    value,
    validAfter,
    validBefore,
    nonce: auth.nonce.toLowerCase(),
  };
  const digest = usdc.transferAuthDigest(domainSepBytes, normalized);
  const digestHex = evm.bytesToHex(digest);

  let payer;
  try {
    payer = evm.recoverAddress(digest, p.signature);
  } catch (e) {
    throw new VerifyReject('invalid_exact_evm_payload_signature', e.message);
  }
  if (!evm.addressEquals(payer, auth.from)) {
    throw new VerifyReject('invalid_exact_evm_payload_signature', 'signature does not recover to authorization.from');
  }

  return { requirements: req, authorization: normalized, signature: p.signature, digest, digestHex, payer };
}

/**
 * Full verification: structure + chain-time window + balance + unused nonce.
 * `deps` = { cfg, rpc, store (may be null for pure-stateless), domainSepBytes,
 * nowChainSeconds? } — nowChainSeconds is injectable for tests.
 */
async function verifyPayment(body, deps) {
  const { cfg, rpc, store, domainSepBytes } = deps;
  const facts = checkPayload(body, cfg, domainSepBytes);
  const { authorization: auth, payer } = facts;

  // Validity window against CHAIN time (block timestamp), not wall clock.
  let nowSec;
  if (deps.nowChainSeconds !== undefined) {
    nowSec = BigInt(deps.nowChainSeconds);
  } else {
    let block;
    try {
      block = await rpc.call('eth_getBlockByNumber', ['latest', false]);
    } catch (e) {
      throw new VerifyReject('unexpected_verify_error', `rpc latest block failed: ${e.message}`);
    }
    if (!block || !block.timestamp) throw new VerifyReject('unexpected_verify_error', 'latest block unavailable');
    nowSec = evm.quantityToBigInt(block.timestamp);
  }
  if (nowSec < auth.validAfter) {
    throw new VerifyReject('invalid_exact_evm_payload_authorization_valid_after', 'authorization not yet valid');
  }
  if (nowSec + BigInt(cfg.expiryMarginSeconds) >= auth.validBefore) {
    throw new VerifyReject('invalid_exact_evm_payload_authorization_valid_before', 'authorization expired or expires too soon to settle');
  }

  // Payer balance.
  let balance;
  try {
    balance = evm.quantityToBigInt(
      await rpc.call('eth_call', [{ to: cfg.asset, data: usdc.balanceOfCalldata(auth.from) }, 'latest'])
    );
  } catch (e) {
    throw new VerifyReject('unexpected_verify_error', `balanceOf failed: ${e.message}`);
  }
  if (balance < auth.value) {
    throw new VerifyReject('insufficient_funds', `payer holds ${balance}, needs ${auth.value}`);
  }

  // Nonce truly unused on-chain (the chain's own replay ledger).
  let state;
  try {
    state = await rpc.call('eth_call', [
      { to: cfg.asset, data: usdc.authorizationStateCalldata(auth.from, auth.nonce) },
      'latest',
    ]);
  } catch (e) {
    throw new VerifyReject('unexpected_verify_error', `authorizationState failed: ${e.message}`);
  }
  if (BigInt(state) !== 0n) {
    throw new VerifyReject('invalid_transaction_state', 'authorization nonce already used or canceled on-chain');
  }

  // Our persistent ledger (read-only here): claimed/settled => replay.
  // /settle passes skipStoreCheck and lets the ATOMIC claim be the sole DB
  // arbiter (no check-then-act window; settled rows answer idempotently).
  if (store && !deps.skipStoreCheck) {
    const row = store.getByAuthorizationHash(facts.digestHex);
    if (row && !replayableRow(row)) {
      throw new VerifyReject('invalid_transaction_state', `authorization already ${row.status} (payment ${row.payment_id})`);
    }
  }

  return facts;
}

/**
 * A stored row that does NOT block re-use: a failed attempt that never
 * signed/broadcast a tx (nonce untouched on-chain) or an expired claim.
 */
function replayableRow(row) {
  return (row.status === 'failed' && !row.raw_tx) || row.status === 'expired';
}

module.exports = { VerifyReject, checkPayload, verifyPayment, replayableRow };
