'use strict';
/**
 * EIP-3009 / USDC (FiatTokenV2_2) specifics: selectors, event topics, typed
 * data, calldata encoding and receipt-log validation for
 * transferWithAuthorization.
 *
 * All constants below were live-verified against Base mainnet USDC
 * (0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913) on 2026-08-15:
 * DOMAIN_SEPARATOR() == 0x02fa7265e7c5d81118673727957699e4d68f74cd74b7db77
 * da710fe8a2c7834f, name() == "USD Coin", version() == "2", decimals() == 6,
 * eth_chainId == 0x2105. The facilitator re-checks the domain separator
 * against the LIVE contract at startup/readyz — a mismatch fails closed
 * (catches wrong token / wrong chain / wrong domain name in one check).
 */

const evm = require('./evm');

// Function selectors (keccak-256 of the canonical signatures).
const SELECTORS = {
  // transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,uint8,bytes32,bytes32)
  transferWithAuthorization: '0xe3ee160e',
  // authorizationState(address,bytes32) -> bool
  authorizationState: '0xe94a0102',
  // balanceOf(address)
  balanceOf: '0x70a08231',
  // DOMAIN_SEPARATOR()
  domainSeparator: '0x3644e515',
  // name() / version() / decimals()
  name: '0x06fdde03',
  version: '0x54fd4d50',
  decimals: '0x313ce567',
};

// Event topic0 hashes.
const TOPICS = {
  transfer: '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
  authorizationUsed: '0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5',
};

// keccak256("TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)")
const TRANSFER_WITH_AUTHORIZATION_TYPEHASH = evm.hexToBytes(
  '0x7c7c6cdb67a18743f49ec6fa9b35f50d52ed05cbed4cc592e13b44501c1a2267'
);

function addrWord(addr) {
  return '000000000000000000000000' + evm.strip0x(addr).toLowerCase();
}

/** structHash for a TransferWithAuthorization message. Amounts are BigInt. */
function transferAuthStructHash({ from, to, value, validAfter, validBefore, nonce }) {
  const enc = Buffer.concat([
    TRANSFER_WITH_AUTHORIZATION_TYPEHASH,
    evm.hexToBytes('0x' + addrWord(from)),
    evm.hexToBytes('0x' + addrWord(to)),
    evm.bigintToWord(value),
    evm.bigintToWord(validAfter),
    evm.bigintToWord(validBefore),
    evm.hexToBytes(nonce),
  ]);
  return evm.keccak(enc);
}

/** The EIP-712 digest the payer signed. domainSep is 32 raw bytes. */
function transferAuthDigest(domainSep, auth) {
  return evm.eip712Digest(domainSep, transferAuthStructHash(auth));
}

/** Calldata for transferWithAuthorization(...v,r,s) from auth + eth signature. */
function transferAuthCalldata(auth, sigHex) {
  const { r, s, v } = evm.parseEthSignature(sigHex);
  return (
    SELECTORS.transferWithAuthorization +
    addrWord(auth.from) +
    addrWord(auth.to) +
    Buffer.from(evm.bigintToWord(auth.value)).toString('hex') +
    Buffer.from(evm.bigintToWord(auth.validAfter)).toString('hex') +
    Buffer.from(evm.bigintToWord(auth.validBefore)).toString('hex') +
    evm.strip0x(auth.nonce).toLowerCase() +
    v.toString(16).padStart(64, '0') +
    Buffer.from(r).toString('hex') +
    Buffer.from(s).toString('hex')
  );
}

/** Calldata for authorizationState(authorizer, nonce). */
function authorizationStateCalldata(authorizer, nonce) {
  return SELECTORS.authorizationState + addrWord(authorizer) + evm.strip0x(nonce).toLowerCase();
}

/** Calldata for balanceOf(addr). */
function balanceOfCalldata(addr) {
  return SELECTORS.balanceOf + addrWord(addr);
}

/**
 * Validate that a settlement receipt actually did what we paid gas for:
 * status 0x1, an AuthorizationUsed(authorizer=payer, nonce) log from the
 * token contract, and a Transfer(from=payer, to=payTo) log carrying exactly
 * `value`. "Do not trust status alone."
 * Returns { ok, reason }.
 */
function validateSettlementReceipt(receipt, { token, payer, payTo, value, nonce }) {
  if (!receipt || receipt.status !== '0x1') return { ok: false, reason: 'receipt status not success' };
  const logs = Array.isArray(receipt.logs) ? receipt.logs : [];
  const fromToken = logs.filter((l) => evm.addressEquals(l.address, token));
  const authUsed = fromToken.some(
    (l) =>
      Array.isArray(l.topics) &&
      l.topics.length >= 3 &&
      l.topics[0] === TOPICS.authorizationUsed &&
      wordIsAddress(l.topics[1], payer) &&
      l.topics[2].toLowerCase() === nonce.toLowerCase()
  );
  if (!authUsed) return { ok: false, reason: 'AuthorizationUsed log missing or mismatched' };
  const transfer = fromToken.some(
    (l) =>
      Array.isArray(l.topics) &&
      l.topics.length >= 3 &&
      l.topics[0] === TOPICS.transfer &&
      wordIsAddress(l.topics[1], payer) &&
      wordIsAddress(l.topics[2], payTo) &&
      wordValue(l.data) === value
  );
  if (!transfer) return { ok: false, reason: 'Transfer log missing or mismatched' };
  return { ok: true };
}

function wordIsAddress(topicWord, addr) {
  if (typeof topicWord !== 'string' || !evm.isHex(topicWord, 32)) return false;
  return '0x' + evm.strip0x(topicWord).slice(24).toLowerCase() === addr.toLowerCase();
}

function wordValue(dataHex) {
  if (typeof dataHex !== 'string' || !evm.isHex(dataHex, 32)) return null;
  return BigInt(dataHex);
}

module.exports = {
  SELECTORS,
  TOPICS,
  TRANSFER_WITH_AUTHORIZATION_TYPEHASH,
  transferAuthStructHash,
  transferAuthDigest,
  transferAuthCalldata,
  authorizationStateCalldata,
  balanceOfCalldata,
  validateSettlementReceipt,
};
