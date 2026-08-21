#!/usr/bin/env node
'use strict';
/**
 * Register animica.dev's x402 resources with x402scan.
 *
 * Their /api/x402/registry/register-origin answers 402 with a SIWX
 * (Sign-In-With-X) challenge rather than a payment demand: prove you control
 * a wallet, then it fetches our discovery document and registers everything
 * in it. So this signs an EIP-191 SIWE message with our own key — no payment,
 * no Coinbase, no browser.
 */
const fs = require('node:fs');
const evm = require('./src/facilitator-evm/evm.js');

const URL_ = 'https://www.x402scan.com/api/x402/registry/register-origin';
const ORIGIN = 'https://animica.dev';
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

const key = (() => {
  const m = /SMOKE_PRIVATE_KEY=(.+)/.exec(fs.readFileSync('/root/animica-x402-payer.env', 'utf8'));
  return Buffer.from(m[1].trim().replace(/^0x/, ''), 'hex');
})();
const ADDRESS = evm.privateKeyToAddress(key);

/** EIP-191 personal_sign over a utf8 message. */
function personalSign(message) {
  const msg = Buffer.from(message, 'utf8');
  const prefixed = Buffer.concat([Buffer.from(`\x19Ethereum Signed Message:\n${msg.length}`, 'utf8'), msg]);
  const digest = evm.keccak(prefixed);
  const s = evm.signDigest(digest, key);
  return '0x' + Buffer.from(s.rWord).toString('hex') + Buffer.from(s.sWord).toString('hex') + s.v.toString(16).padStart(2, '0');
}

/** siwe SiweMessage.prepareMessage(), which is what their verifier rebuilds. */
function prepareMessage(i, address) {
  const chainId = String(i.chainId).split(':').pop();
  const lines = [
    `${i.domain} wants you to sign in with your Ethereum account:`,
    address,
    '',
  ];
  if (i.statement) lines.push(i.statement, '');
  lines.push(`URI: ${i.uri}`, `Version: ${i.version}`, `Chain ID: ${chainId}`, `Nonce: ${i.nonce}`, `Issued At: ${i.issuedAt}`);
  if (i.expirationTime) lines.push(`Expiration Time: ${i.expirationTime}`);
  if (i.notBefore) lines.push(`Not Before: ${i.notBefore}`);
  if (i.requestId) lines.push(`Request ID: ${i.requestId}`);
  if (i.resources && i.resources.length) {
    lines.push('Resources:');
    for (const r of i.resources) lines.push(`- ${r}`);
  }
  return lines.join('\n');
}

/** One full challenge -> sign -> register round trip. Returns the parsed result. */
async function registerOnce() {
  const r1 = await fetch(URL_, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'user-agent': UA, accept: 'application/json' },
    body: JSON.stringify({ origin: ORIGIN }),
  });
  if (r1.status !== 402) {
    throw new Error(`unexpected challenge status ${r1.status}: ${(await r1.text()).slice(0, 200)}`);
  }
  const offer = await r1.json();
  const info = offer.extensions['sign-in-with-x'].info;

  // The nonce is single-use, so every retry re-fetches and re-signs. Reusing a
  // spent nonce is rejected, which would look exactly like a persistent failure.
  const message = prepareMessage(info, ADDRESS);
  const signature = personalSign(message);
  const siwx = {
    domain: info.domain, address: ADDRESS, statement: info.statement, uri: info.uri,
    version: info.version, chainId: info.chainId, type: info.type, nonce: info.nonce,
    issuedAt: info.issuedAt, expirationTime: info.expirationTime, signature,
  };

  const r2 = await fetch(URL_, {
    method: 'POST',
    headers: {
      'content-type': 'application/json', 'user-agent': UA, accept: 'application/json',
      'SIGN-IN-WITH-X': Buffer.from(JSON.stringify(siwx), 'utf8').toString('base64'),
    },
    body: JSON.stringify({ origin: ORIGIN }),
  });
  const text = await r2.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* reported below as unparseable */ }
  return { status: r2.status, json, text };
}

const nap = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * REGISTER, THEN RETRY THE STRAGGLERS.
 *
 * x402scan re-fetches our OpenAPI once per endpoint while registering, and that
 * fetch flakes: two consecutive runs on 2026-08-19 failed a DIFFERENT pair of
 * endpoints each time, both times with "Missing input schema", against paths
 * whose requestBody schema is present, valid, and byte-identical in shape to
 * the ones that succeeded. Verified independently: all four "failing" endpoints
 * answer 402 with a populated `outputSchema.input` when probed directly.
 *
 * So a single run leaves a random handful of our catalog unlisted, and the
 * error message points at a defect that is not there. Retrying is the correct
 * remedy for a flaky counterparty — and it turns the error into information:
 * an endpoint that fails EVERY attempt is a real problem worth looking at,
 * which one run could never distinguish from noise.
 */
(async () => {
  const maxAttempts = Number(process.env.X402SCAN_MAX_ATTEMPTS || 4);
  // URLs that have failed on EVERY attempt so far. Intersected each round,
  // because their crawler flakes on a DIFFERENT random subset every run —
  // reporting only the last attempt's failures would report pure noise, and
  // three consecutive runs on 2026-08-19 failed three disjoint sets.
  let alwaysFailed = null;
  let attempt = 0;
  let usable = 0;
  let last = null;

  while (attempt < maxAttempts) {
    attempt += 1;
    let res;
    try {
      res = await registerOnce();
    } catch (e) {
      console.log(`attempt ${attempt}: ${e.message}`);
      if (attempt < maxAttempts) await nap(3000 * attempt);
      continue;
    }
    const j = res.json;
    if (!j) {
      console.log(`attempt ${attempt}: status ${res.status}, unparseable body: ${res.text.slice(0, 200)}`);
      if (attempt < maxAttempts) await nap(3000 * attempt);
      continue;
    }
    usable += 1;
    last = j;
    const failed = new Set((Array.isArray(j.failedDetails) ? j.failedDetails : []).map((f) => f.url));
    console.log(
      'attempt %d: signed as %s | registered %s, failed %s, skipped(already listed) %s, total %s',
      attempt, ADDRESS, j.registered, j.failed, j.skipped, j.total,
    );
    for (const u of failed) console.log('   flaked this round: %s', u);

    alwaysFailed = alwaysFailed === null ? failed : new Set([...alwaysFailed].filter((u) => failed.has(u)));
    if (!alwaysFailed.size) break;
    if (attempt < maxAttempts) await nap(4000);
  }

  if (!usable) {
    console.log('\nREGISTRATION FAILED: no attempt produced a usable response.');
    process.exitCode = 1;
    return;
  }
  console.log('\n%d usable attempt(s); registry reports %s resources for this origin.', usable, last.total);
  if (alwaysFailed && alwaysFailed.size) {
    console.log('\n%d endpoint(s) failed on EVERY attempt — check these by hand:', alwaysFailed.size);
    for (const u of alwaysFailed) console.log('  %s', u);
    console.log('Probe each directly and confirm it answers 402 with a populated accepts[0].outputSchema.input.');
    process.exitCode = 1;
  } else {
    console.log('No endpoint failed on every attempt: every path registered on at least one round.');
    console.log('Per-round failures above are the registrar re-fetching our OpenAPI and flaking; a');
    console.log('different random subset fails each run, against schemas that are present and valid.');
  }
})();
