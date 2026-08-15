#!/usr/bin/env node
// x402 payer smoke test — a standalone client implementing the spec's payer
// flow against a running gateway:
//
//   GET <url>                       -> 402 + PAYMENT-REQUIRED (base64 JSON)
//   pick the eip155:* accepts entry -> sign an EIP-3009 TransferWithAuthorization
//   retry with PAYMENT-SIGNATURE    -> 200 + PAYMENT-RESPONSE (settlement tx)
//   print tx hash + block-explorer URL + response body
//
// Uses ONLY the repo's pinned deps (@noble/secp256k1, @noble/hashes) and the
// global fetch — no viem, no x402 SDK — so it doubles as an independent
// cross-check of the facilitator's EIP-712 digest computation: if this
// script and src/facilitator-evm/ disagreed on the digest, signature
// recovery would not match `authorization.from` and verify would reject.
//
// The EIP-712 domain comes from the accepts entry's `extra.{name,version}`
// when the server advertises it; otherwise from a built-in table of
// LIVE-VERIFIED USDC domains (2026-08-15: Base mainnet name is "USD Coin",
// Base Sepolia is "USDC" — mixing them up makes every signature invalid).
// Note: the gateway's EVM accepts entries currently omit `extra`, so the
// fallback table is what real runs use today (see docs/x402.md, known gaps).
//
// Usage:
//   node test/manual/smoke-pay.mjs keygen
//       Generate a THROWAWAY payer wallet (prints key + address). Testnet
//       use only — never hold meaningful funds on a key that was ever
//       displayed on a terminal.
//
//   SMOKE_PRIVATE_KEY=0x… node test/manual/smoke-pay.mjs [url]
//       Run the payer flow. The wallet must hold USDC on the offered
//       network (Base Sepolia faucet: https://faucet.circle.com).
//
// Environment:
//   SMOKE_URL              paid route (default http://127.0.0.1:8742/x402/paid/echo;
//                          the positional [url] argument overrides it)
//   SMOKE_PRIVATE_KEY      payer key, 32-byte hex (throwaway/testnet only)
//   SMOKE_NETWORK          optional CAIP-2 filter, e.g. eip155:84532 — run
//                          against Sepolia FIRST, mainnet only after that passes
//   SMOKE_IDEMPOTENCY_KEY  optional Idempotency-Key header to send
//   SMOKE_VALID_SECONDS    authorization validity horizon (default
//                          max(maxTimeoutSeconds, 300))

// `node --test test/` treats every file under test/ as a test — this one is
// a MANUAL tool (needs a funded wallet + running gateway), so it no-ops
// cleanly under the automated runner and keeps the suite green.
if (process.env.NODE_TEST_CONTEXT) {
  console.log('# smoke-pay.mjs is a manual payer tool (see base-sepolia.md) — skipped under node --test');
  process.exit(0);
}

import crypto from 'node:crypto';
import * as secp from '@noble/secp256k1';
import { keccak_256 } from '@noble/hashes/sha3.js';
import { hmac } from '@noble/hashes/hmac.js';
import { sha256 } from '@noble/hashes/sha2.js';

// noble v3: wire hashes before sync signing (RFC6979 HMAC-DRBG).
secp.hashes.hmacSha256 = (k, m) => hmac(sha256, k, m);
secp.hashes.sha256 = sha256;

// Live-verified USDC EIP-712 domains + explorers (recon 2026-08-15).
const KNOWN_NETWORKS = {
  'eip155:8453': {
    label: 'Base mainnet',
    usdc: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
    eip712: { name: 'USD Coin', version: '2' },
    explorerTx: 'https://basescan.org/tx/',
  },
  'eip155:84532': {
    label: 'Base Sepolia',
    usdc: '0x036CbD53842c5426634e7929541eC2318f3dCF7e',
    eip712: { name: 'USDC', version: '2' },
    explorerTx: 'https://sepolia.basescan.org/tx/',
  },
};

/* ------------------------------------------------------------- helpers -- */

const strip0x = (h) => (h.startsWith('0x') ? h.slice(2) : h);
const hexToBytes = (h) => Uint8Array.from(Buffer.from(strip0x(h), 'hex'));
const bytesToHex = (b) => '0x' + Buffer.from(b).toString('hex');
const utf8 = (s) => Buffer.from(s, 'utf8');
const word = (v) => {
  // address or bigint -> 32-byte abi word
  if (typeof v === 'bigint') return hexToBytes(v.toString(16).padStart(64, '0'));
  return hexToBytes('0'.repeat(24) + strip0x(v).toLowerCase());
};

function addressOf(privBytes) {
  const pub = secp.getPublicKey(privBytes, false); // 65 bytes, 0x04 prefix
  return bytesToHex(keccak_256(pub.slice(1)).slice(12));
}

// Typehashes computed at runtime (independent of the gateway's constants).
const DOMAIN_TYPEHASH = keccak_256(
  utf8('EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)'));
const AUTH_TYPEHASH = keccak_256(
  utf8('TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)'));

function domainSeparator({ name, version, chainId, verifyingContract }) {
  return keccak_256(Buffer.concat([
    DOMAIN_TYPEHASH,
    keccak_256(utf8(name)),
    keccak_256(utf8(version)),
    word(BigInt(chainId)),
    word(verifyingContract),
  ]));
}

function authDigest(domainSep, auth) {
  const structHash = keccak_256(Buffer.concat([
    AUTH_TYPEHASH,
    word(auth.from), word(auth.to),
    word(auth.value), word(auth.validAfter), word(auth.validBefore),
    hexToBytes(auth.nonce),
  ]));
  return keccak_256(Buffer.concat([Buffer.from([0x19, 0x01]), domainSep, structHash]));
}

/** Sign a digest -> Ethereum wire signature hex (r||s||v, v in {27,28}). */
function ethSign(digest, privBytes) {
  const sig = secp.sign(digest, privBytes, { prehash: false, format: 'recovered' });
  // noble layout: recovery(1) || r(32) || s(32); Ethereum: r || s || v(27|28)
  const out = new Uint8Array(65);
  out.set(sig.slice(1, 65), 0);
  out[64] = 27 + sig[0];
  return bytesToHex(out);
}

const b64ToJson = (v) => JSON.parse(Buffer.from(String(v), 'base64').toString('utf8'));
const jsonToB64 = (o) => Buffer.from(JSON.stringify(o), 'utf8').toString('base64');

function die(msg, code = 1) {
  console.error(`\nFAIL: ${msg}`);
  process.exit(code);
}

/* ---------------------------------------------------------------- main -- */

const argv = process.argv.slice(2);

if (argv[0] === 'keygen') {
  const priv = secp.utils.randomSecretKey();
  console.log('THROWAWAY payer wallet (testnet use only — this key was displayed):');
  console.log(`  SMOKE_PRIVATE_KEY=${bytesToHex(priv)}`);
  console.log(`  address ${addressOf(priv)}`);
  console.log('Fund it with Base Sepolia USDC at https://faucet.circle.com before paying.');
  process.exit(0);
}

const url = (argv[0] !== 'pay' && argv[0]) || argv[1] || process.env.SMOKE_URL
  || 'http://127.0.0.1:8742/x402/paid/echo';
const netFilter = process.env.SMOKE_NETWORK || null;
const keyHex = process.env.SMOKE_PRIVATE_KEY || '';
if (!/^(0x)?[0-9a-fA-F]{64}$/.test(keyHex)) {
  die('set SMOKE_PRIVATE_KEY to a 32-byte hex key (run `smoke-pay.mjs keygen` for a throwaway)');
}
const priv = hexToBytes(keyHex);
const payer = addressOf(priv);

console.log(`payer   ${payer}`);
console.log(`target  ${url}`);

// ---- step 1: unpaid request -> expect 402 + PAYMENT-REQUIRED ------------
const first = await fetch(url, { headers: { accept: 'application/json' } });
if (first.status !== 402) {
  const body = (await first.text()).slice(0, 400);
  if (first.ok) die(`expected 402 but got ${first.status} — is this route actually paid?\n${body}`);
  die(`expected 402 but got ${first.status} (unavailable products answer 503 and never take payment)\n${body}`);
}
const prHeader = first.headers.get('payment-required');
if (!prHeader) die('402 carried no PAYMENT-REQUIRED header (v2 wire)');
const paymentRequired = b64ToJson(prHeader);
const accepts = paymentRequired.accepts || [];
const accepted = accepts.find((a) =>
  a.scheme === 'exact' && a.network.startsWith('eip155:') && (!netFilter || a.network === netFilter));
if (!accepted) {
  die(`no matching exact/eip155 accepts entry${netFilter ? ` for ${netFilter}` : ''} in: `
    + accepts.map((a) => `${a.scheme}@${a.network}`).join(', '));
}
const known = KNOWN_NETWORKS[accepted.network];
console.log(`offer   ${accepted.amount} atomic units of ${accepted.asset}`);
console.log(`        on ${accepted.network}${known ? ` (${known.label})` : ''} -> payTo ${accepted.payTo}`);

// Safety cross-checks before signing anything.
if (known && accepted.asset.toLowerCase() !== known.usdc.toLowerCase()) {
  die(`offered asset ${accepted.asset} is not the known USDC contract for ${accepted.network} (${known.usdc}) — refusing to sign`);
}
const domainMeta = (accepted.extra && accepted.extra.name && accepted.extra.version)
  ? { name: accepted.extra.name, version: accepted.extra.version }
  : known && known.eip712;
if (!domainMeta) die(`no extra.{name,version} advertised and network ${accepted.network} not in the built-in domain table`);
const chainId = Number(accepted.network.split(':')[1]);

// ---- step 2: sign the EIP-3009 authorization locally --------------------
const nowSec = Math.floor(Date.now() / 1000);
const horizon = Number(process.env.SMOKE_VALID_SECONDS || 0)
  || Math.max(Number(accepted.maxTimeoutSeconds) || 60, 300);
const auth = {
  from: payer,
  to: accepted.payTo,
  value: BigInt(accepted.amount),
  validAfter: BigInt(nowSec - 600), // contract requires block.timestamp > validAfter
  validBefore: BigInt(nowSec + horizon),
  nonce: bytesToHex(crypto.randomBytes(32)), // random per authorization, never sequential
};
const domainSep = domainSeparator({
  name: domainMeta.name, version: domainMeta.version, chainId, verifyingContract: accepted.asset,
});
const signature = ethSign(authDigest(domainSep, auth), priv);
console.log(`signed  EIP-3009 nonce ${auth.nonce.slice(0, 18)}… domain "${domainMeta.name}" v${domainMeta.version} chain ${chainId}`);

// ---- step 3: retry with PAYMENT-SIGNATURE -------------------------------
const paymentPayload = {
  x402Version: 2,
  resource: paymentRequired.resource && paymentRequired.resource.url,
  accepted, // echoed VERBATIM — the gateway matches it against its own offer
  payload: {
    signature,
    authorization: {
      from: auth.from,
      to: auth.to,
      value: auth.value.toString(),
      validAfter: auth.validAfter.toString(),
      validBefore: auth.validBefore.toString(),
      nonce: auth.nonce,
    },
  },
};
const headers = { accept: 'application/json', 'payment-signature': jsonToB64(paymentPayload) };
if (process.env.SMOKE_IDEMPOTENCY_KEY) headers['idempotency-key'] = process.env.SMOKE_IDEMPOTENCY_KEY;

const paid = await fetch(url, { headers });
const bodyText = await paid.text();
const settlementHeader = paid.headers.get('payment-response');
const settlement = settlementHeader ? b64ToJson(settlementHeader) : null;

// ---- step 4: report ------------------------------------------------------
console.log(`\nHTTP ${paid.status}`);
if (settlement) {
  console.log(`settlement success=${settlement.success} network=${settlement.network}`
    + (settlement.payer ? ` payer=${settlement.payer}` : '')
    + (settlement.errorReason ? ` errorReason=${settlement.errorReason}` : ''));
  if (settlement.transaction) {
    console.log(`tx      ${settlement.transaction}`);
    if (known) console.log(`explorer ${known.explorerTx}${settlement.transaction}`);
  }
}
console.log('\nresponse body:');
console.log(bodyText.length > 3000 ? bodyText.slice(0, 3000) + `\n… (${bodyText.length} bytes total)` : bodyText);

if (paid.status === 402) {
  let reason = '';
  try { reason = JSON.parse(bodyText).error || ''; } catch { /* not json */ }
  die(`payment rejected (${reason || 'see body'}) — common causes: no USDC balance on `
    + `${accepted.network}, expired authorization, or a domain-name mismatch`, 2);
}
if (!paid.ok) die(`paid request failed with ${paid.status}`);
if (!settlement || settlement.success !== true) die('200 without a successful PAYMENT-RESPONSE header');
console.log('\nOK: paid, settled, delivered.');
