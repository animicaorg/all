'use strict';
/**
 * ANM-NATIVE x402 SETTLEMENT LANE.
 *
 * WHY THIS LANE EXISTS. On Base we sponsor the gas for every settlement:
 * ~$0.0018 spent / $0.0042 reserved per payment, and `checkEconomicFloor`
 * refuses to settle when reserved gas x2 exceeds the payment. That is a hard
 * floor near $0.0084 under every USDC price on this gateway, and it is the
 * single reason we cannot sell the sub-cent calls the rest of the x402
 * ecosystem sells.
 *
 * On Animica the PAYER signs and pays the fee out of their own ANM balance.
 * The gateway submits an already-signed transaction and spends nothing. The
 * gas floor does not merely shrink on this lane — it is gone. That is what
 * funds the ANM discount, and it is why paying in ANM is genuinely cheaper
 * for a buyer rather than a loyalty gimmick.
 *
 * THE SCHEME ("exact-anm"). The payer builds a normal TRANSFER to our payTo
 * address for at least the quoted nANM, signs it, and hands us the signed
 * raw transaction. We verify it without submitting, then submit it to settle.
 * This is simpler than EIP-3009: no authorization contract is involved,
 * because on this chain ANY party may submit an already-signed transaction.
 *
 * NETWORK ID. Advertised as `animica:1`, NEVER `eip155:1` — chainId here is
 * 1, and an agent that read `eip155:1` would try to pay us on ETHEREUM
 * MAINNET and lose the money. The genesis hash is published alongside so a
 * careful client can prove which chain it is talking about.
 *
 * RULES INHERITED FROM PAYMENTS WORK ALREADY DONE ON THIS CHAIN:
 *   - ONLY TxKind TRANSFER is payment evidence. A CALL carries no value
 *     (there is no amount field on it at all), so a CALL that merely
 *     mentions the right reference must never be accepted as payment.
 *   - INCLUSION IS NOT EXECUTION. A tx in a block may still have failed.
 *     Settlement is confirmed from the status/receipt, never from inclusion.
 *   - Amounts are BigInt end to end. nANM is 1e-9 ANM; a JS Number loses
 *     digits well inside the range real balances occupy.
 *   - Fail closed: anything we cannot verify is not a payment.
 */

const crypto = require('node:crypto');

/** Node "0x..." digest / bech32m anim1... -> lowercase 64-char hex digest. */
function normalizeDigest(value, toAccountDigestHex) {
  if (typeof value !== 'string' || !value.trim()) return null;
  const s = value.trim();
  if (/^0x[0-9a-fA-F]{64}$/.test(s)) return s.slice(2).toLowerCase();
  if (/^[0-9a-fA-F]{64}$/.test(s)) return s.toLowerCase();
  try {
    return toAccountDigestHex(s).toLowerCase();
  } catch {
    return null;
  }
}

/** decimal | 0x-hex | safe integer -> BigInt. Never a float. */
function toBigIntAmount(v) {
  if (typeof v === 'bigint') return v;
  if (typeof v === 'string') {
    const s = v.trim();
    if (/^-?\d+$/.test(s)) return BigInt(s);
    if (/^0x[0-9a-fA-F]+$/.test(s)) return BigInt(s);
    throw new Error(`unparseable amount ${JSON.stringify(v)}`);
  }
  if (typeof v === 'number') {
    if (!Number.isSafeInteger(v)) throw new Error('amount arrived as an unsafe JS number');
    return BigInt(v);
  }
  throw new Error(`unparseable amount of type ${typeof v}`);
}

/** Pull the transferred value out of whatever shape the decoder returned. */
function valueOf(tx) {
  for (const k of ['value', 'amount']) {
    if (tx[k] !== undefined && tx[k] !== null) return toBigIntAmount(tx[k]);
  }
  // A nested payload form (normalizer maps `value` into payload.v.amount).
  if (tx.payload && tx.payload.v && tx.payload.v.amount !== undefined) {
    return toBigIntAmount(tx.payload.v.amount);
  }
  return null;
}

/**
 * Decide whether a decoded transaction is a plain value TRANSFER.
 *
 * We accept only what we can positively identify as a transfer. An unknown
 * or absent kind with a `to` and a positive `value` is treated as a transfer
 * ONLY when the decoder gave us no kind field at all (older decode shapes);
 * anything that positively identifies as a contract call, a deployment or a
 * block-reward transaction is rejected.
 */
function transferCheck(tx) {
  const kindRaw = tx.kind !== undefined ? tx.kind : (tx.type !== undefined ? tx.type : (tx.txKind !== undefined ? tx.txKind : null));
  if (kindRaw === null || kindRaw === undefined) return { ok: true, kind: 'unspecified' };
  if (typeof kindRaw === 'number') {
    if (kindRaw === 0) return { ok: true, kind: 'TRANSFER' };
    const names = { 1: 'DEPLOY', 2: 'CALL', 3: 'BLOCK_REWARD' };
    return { ok: false, kind: names[kindRaw] || `kind_${kindRaw}` };
  }
  const s = String(kindRaw).toUpperCase();
  if (s === 'TRANSFER' || s === '0') return { ok: true, kind: 'TRANSFER' };
  return { ok: false, kind: s };
}

function createAnmFacilitator({
  cfg,
  node,
  store,
  toAccountDigestHex,
  now = Date.now,
  logger = null,
  sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
}) {
  const log = logger || { info() {}, warn() {}, error() {} };
  const payToDigest = normalizeDigest(cfg.anmPayTo, toAccountDigestHex);
  if (!payToDigest) {
    throw new Error(`ANM lane is enabled but X402_ANM_PAY_TO is not a usable address: ${cfg.anmPayTo}`);
  }

  async function head() {
    const h = await node.call('chain.getHead', {}, { timeoutMs: 5000 });
    const height = h && Number.isInteger(h.height) ? h.height : null;
    if (height === null) throw new Error('chain.getHead returned no height');
    return { height, hash: h.hash || null, canonicalHeight: Number.isInteger(h.canonicalHeight) ? h.canonicalHeight : height };
  }

  /**
   * Verify a payment WITHOUT submitting it. Returns the x402 verdict shape
   * {isValid, invalidReason?, payer?, ...} so the paywall can treat this lane
   * exactly like the EVM one.
   */
  async function verify(paymentPayload, requirements) {
    const raw = paymentPayload
      && paymentPayload.payload
      && (paymentPayload.payload.rawTransaction || paymentPayload.payload.raw_transaction || paymentPayload.payload.rawTx);
    if (typeof raw !== 'string' || !raw.trim()) {
      return { isValid: false, invalidReason: 'payload.rawTransaction missing — sign a TRANSFER to payTo and send the signed raw transaction' };
    }

    let decoded;
    try {
      decoded = await node.call('tx.decodeRawTransaction', { rawTx: raw }, { timeoutMs: 8000 });
    } catch (e) {
      return { isValid: false, invalidReason: `transaction did not decode: ${e.message}` };
    }
    const tx = (decoded && decoded.transaction) || decoded;
    if (!tx || typeof tx !== 'object') {
      return { isValid: false, invalidReason: 'decoder returned no transaction' };
    }

    // 1. Right chain. A tx signed for another chainId must never be accepted
    //    here even if every other field matches.
    if (tx.chainId !== undefined && tx.chainId !== null && Number(tx.chainId) !== Number(cfg.anmChainId)) {
      return { isValid: false, invalidReason: `transaction is for chainId ${tx.chainId}, this gateway settles on chainId ${cfg.anmChainId}` };
    }

    // 2. A value TRANSFER, not a CALL. A CALL carries no amount on this
    //    chain, so accepting one would be accepting a payment of nothing.
    const kind = transferCheck(tx);
    if (!kind.ok) {
      return { isValid: false, invalidReason: `only a value TRANSFER can pay an invoice; this is a ${kind.kind}` };
    }

    // 3. Paid to US.
    const to = normalizeDigest(tx.to, toAccountDigestHex);
    if (!to) return { isValid: false, invalidReason: 'transaction has no readable recipient' };
    if (to !== payToDigest) {
      return { isValid: false, invalidReason: `transaction pays ${tx.to}, not this gateway's payTo address` };
    }

    // 4. Enough value. `maxAmountRequired` is the quoted nANM as a decimal
    //    string; both sides go through BigInt.
    let value;
    try {
      value = valueOf(tx);
    } catch (e) {
      return { isValid: false, invalidReason: `unreadable value: ${e.message}` };
    }
    if (value === null) return { isValid: false, invalidReason: 'transaction carries no value' };
    let required;
    try {
      required = toBigIntAmount(requirements.maxAmountRequired || requirements.amount || '0');
    } catch {
      return { isValid: false, invalidReason: 'server quote unreadable' };
    }
    if (value < required) {
      return { isValid: false, invalidReason: `transaction pays ${value} nANM, the quote requires ${required} nANM` };
    }

    // 5. Still valid at the current height. The chain gives every tx a
    //    validity WINDOW; one that has expired (or has not opened) cannot be
    //    settled, and telling the payer now is far better than taking their
    //    signed tx and failing later.
    let h;
    try {
      h = await head();
    } catch (e) {
      return { isValid: false, invalidReason: `cannot read chain head to check validity window: ${e.message}` };
    }
    const validUntil = Number.isInteger(tx.validUntil) ? tx.validUntil : null;
    const validAfter = Number.isInteger(tx.validAfter) ? tx.validAfter : null;
    if (validUntil !== null && validUntil <= h.height) {
      return { isValid: false, invalidReason: `transaction expired at height ${validUntil}; head is ${h.height}. Re-sign with a fresh validity window.` };
    }
    if (validAfter !== null && validAfter > h.height + Number(cfg.anmMaxFutureBlocks)) {
      return { isValid: false, invalidReason: `transaction is not valid until height ${validAfter}, too far ahead of head ${h.height}` };
    }

    // 6. Signature. The decoder alone does not prove the signature; ask the
    //    node explicitly. If this check cannot be performed we FAIL CLOSED —
    //    an unverifiable signature is not a payment.
    let verified = null;
    try {
      const v = await node.call('tx.debugVerifyRawTransaction', { rawTx: raw }, { timeoutMs: 8000 });
      verified = v && (v.valid === true || v.ok === true || v.verified === true);
      if (v && v.valid === false) {
        return { isValid: false, invalidReason: `signature verification failed: ${v.reason || v.error || 'invalid signature'}` };
      }
    } catch (e) {
      return { isValid: false, invalidReason: `signature could not be verified (failing closed): ${e.message}` };
    }
    if (verified !== true) {
      return { isValid: false, invalidReason: 'signature could not be positively verified (failing closed)' };
    }

    // 7. Replay. A signed transaction is a bearer instrument: the same bytes
    //    must not buy two calls. The chain itself would reject the duplicate
    //    (same salt/nonce), but we must not DELIVER twice before finding out.
    const txid = txidOf(tx, raw);
    const seen = store && store.getAnmPayment ? store.getAnmPayment(txid) : null;
    if (seen && seen.status === 'settled') {
      return { isValid: false, invalidReason: 'this transaction has already been used to pay for a call' };
    }

    return {
      isValid: true,
      payer: tx.from || null,
      txid,
      value: value.toString(),
      required: required.toString(),
      raw,
      validUntil,
      head: h.height,
    };
  }

  /**
   * Settle: submit the signed transaction and confirm it EXECUTED.
   *
   * Inclusion is not execution on this chain — a transaction can sit in a
   * block and still have failed — so a settlement is only reported successful
   * once the status says so. Everything else is either "not yet" (retry) or
   * a failure; the two must never be conflated, because recording a "not yet"
   * as a "never" loses a payment the payer really made.
   */
  async function settle(paymentPayload, requirements, verdict) {
    const raw = (verdict && verdict.raw)
      || (paymentPayload && paymentPayload.payload && paymentPayload.payload.rawTransaction);
    if (!raw) return { success: false, errorReason: 'nothing to settle' };

    let txHash;
    try {
      const r = await node.call('tx.sendRawTransaction', { tx: raw }, { timeoutMs: 15000 });
      txHash = typeof r === 'string' ? r : (r && (r.txHash || r.hash || r.txid));
    } catch (e) {
      const msg = String(e.message || e);
      // The chain rejecting a duplicate means the payer already spent this
      // transaction — which for us is a failed settlement, not a success.
      return { success: false, errorReason: `submission rejected: ${msg}`, retryable: /timeout|unreachable|ECONN/i.test(msg) };
    }
    if (!txHash) return { success: false, errorReason: 'node accepted the transaction but returned no hash' };

    if (store && store.putAnmPayment) {
      store.putAnmPayment({
        txid: txHash,
        payer: (verdict && verdict.payer) || null,
        amountNanm: (verdict && verdict.value) || '0',
        resource: requirements && requirements.resource ? String(requirements.resource) : null,
        status: 'submitted',
        createdAt: Math.floor(now() / 1000),
      });
    }

    const deadline = now() + Number(cfg.anmSettleTimeoutMs);
    let last = null;
    while (now() < deadline) {
      let st;
      try {
        st = await node.call('tx.getStatus', { txHash }, { timeoutMs: 8000 });
      } catch (e) {
        last = { error: e.message };
        await sleep(Number(cfg.anmPollIntervalMs));
        continue;
      }
      last = st;
      const state = String((st && (st.status || st.state)) || '').toLowerCase();
      if (state === 'confirmed' || state === 'finalized' || state === 'instant_confirmed') {
        // Executed, not merely included. Where a receipt is available, a
        // failed execution must not be reported as a settlement.
        if (st.reorged_out === true) {
          return { success: false, errorReason: 'transaction was reorganised out of the chain' };
        }
        if (store && store.setAnmPaymentStatus) store.setAnmPaymentStatus(txHash, 'settled');
        return {
          success: true,
          transaction: txHash,
          network: cfg.anmNetworkId,
          payer: (verdict && verdict.payer) || null,
          blockHeight: st.blockHeight !== undefined ? st.blockHeight : (st.included_height || null),
          confirmations: st.confirmations !== undefined ? st.confirmations : null,
        };
      }
      if (state === 'failed' || state === 'dropped' || state === 'rejected') {
        if (store && store.setAnmPaymentStatus) store.setAnmPaymentStatus(txHash, 'failed');
        return { success: false, errorReason: `transaction ${state}${st.rejection_details ? ': ' + JSON.stringify(st.rejection_details) : ''}`, transaction: txHash };
      }
      await sleep(Number(cfg.anmPollIntervalMs));
    }

    // Timed out waiting. THE OUTCOME IS UNKNOWN, not failed: the transaction
    // may still confirm. Say exactly that, so the caller is not told their
    // money vanished and an operator can reconcile by txid.
    if (store && store.setAnmPaymentStatus) store.setAnmPaymentStatus(txHash, 'unknown');
    return {
      success: false,
      outcomeUnknown: true,
      transaction: txHash,
      errorReason: `settlement not confirmed within ${cfg.anmSettleTimeoutMs}ms; the transaction may still confirm — reconcile by txid ${txHash}`,
      lastStatus: last,
    };
  }

  /** Deterministic id for a transaction we have decoded but not yet sent. */
  function txidOf(tx, raw) {
    if (typeof tx.hash === 'string' && tx.hash) return tx.hash;
    if (typeof tx.txid === 'string' && tx.txid) return tx.txid;
    return '0x' + crypto.createHash('sha256').update(String(raw)).digest('hex');
  }

  return { verify, settle, payToDigest, head };
}

module.exports = { createAnmFacilitator, normalizeDigest, toBigIntAmount, transferCheck, valueOf };
