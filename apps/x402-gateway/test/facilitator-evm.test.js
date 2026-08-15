'use strict';
/**
 * Self-hosted exact-EVM facilitator tests: verification matrix, atomic
 * double-settle, crash recovery, gas caps, readiness gates, and the HTTP
 * surface. Mock RPC only; every key is a throwaway generated in-test.
 */

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');

const evm = require('../src/facilitator-evm/evm');
const { scene } = require('./evm-helpers');
const { createEvmFacilitatorServer } = require('../src/facilitator-evm/server');

/* -------------------------------------------------------------- verify -- */

test('verify: valid payment is valid, payer recovered', async () => {
  const sc = scene();
  const out = await sc.facilitator.verify(sc.payment());
  assert.deepEqual(out, { isValid: true, payer: sc.payer.address });
});

test('verify: tampered signature and wrong-key signature rejected', async () => {
  const sc = scene();
  const wrongKey = await sc.facilitator.verify(sc.payment({ signWithKey: require('./evm-helpers').kp().priv }));
  assert.equal(wrongKey.isValid, false);
  assert.equal(wrongKey.invalidReason, 'invalid_exact_evm_payload_signature');

  const flipped = await sc.facilitator.verify(sc.payment({
    tamper: (b) => {
      // flip one byte of r
      const sig = b.paymentPayload.payload.signature;
      const bad = sig.slice(0, 10) + (sig[10] === 'a' ? 'b' : 'a') + sig.slice(11);
      b.paymentPayload.payload.signature = bad;
      return b;
    },
  }));
  assert.equal(flipped.isValid, false);
  assert.equal(flipped.invalidReason, 'invalid_exact_evm_payload_signature');
});

test('verify: EIP-712 domain mismatch (sepolia domain on mainnet) fails signature check', async () => {
  const sc = scene();
  const wrongDomain = evm.domainSeparator({
    name: 'USDC', version: '2', chainId: 84532,
    verifyingContract: '0x036CbD53842c5426634e7929541eC2318f3dCF7e',
  });
  const out = await sc.facilitator.verify(sc.payment({ signWithDomain: wrongDomain }));
  assert.equal(out.isValid, false);
  assert.equal(out.invalidReason, 'invalid_exact_evm_payload_signature');
});

test('verify: wrong recipient / wrong chain / wrong token / wrong amount / expiry', async () => {
  const sc = scene();

  const wrongTo = await sc.facilitator.verify(sc.payment({ to: '0x' + '55'.repeat(20) }));
  assert.equal(wrongTo.invalidReason, 'invalid_exact_evm_payload_recipient_mismatch');

  const wrongReqPayTo = await sc.facilitator.verify(sc.payment({ reqPayTo: '0x' + '66'.repeat(20) }));
  assert.equal(wrongReqPayTo.invalidReason, 'invalid_payment_requirements');

  const wrongChain = await sc.facilitator.verify(sc.payment({ network: 'eip155:1' }));
  assert.equal(wrongChain.invalidReason, 'invalid_network');

  const wrongToken = await sc.facilitator.verify(sc.payment({ asset: '0x' + '77'.repeat(20) }));
  assert.equal(wrongToken.invalidReason, 'invalid_payment_requirements');

  const wrongAmount = await sc.facilitator.verify(sc.payment({ amount: '20000' })); // signed 10000
  assert.equal(wrongAmount.invalidReason, 'invalid_exact_evm_payload_authorization_value_mismatch');

  const expired = await sc.facilitator.verify(sc.payment({ validBefore: 1_600_000_000n }));
  assert.equal(expired.invalidReason, 'invalid_exact_evm_payload_authorization_valid_before');

  const notYet = await sc.facilitator.verify(sc.payment({ validAfter: 1_900_000_000n }));
  assert.equal(notYet.invalidReason, 'invalid_exact_evm_payload_authorization_valid_after');

  // expiring INSIDE the settle margin is as bad as expired
  const knife = await sc.facilitator.verify(sc.payment({ validBefore: 1_700_000_003n })); // margin is 6s
  assert.equal(knife.invalidReason, 'invalid_exact_evm_payload_authorization_valid_before');
});

test('verify: consumed nonce (chain) and insufficient funds rejected', async () => {
  const sc = scene();
  const body = sc.payment();
  const a = sc.lastAuth;
  sc.rpc.state.authConsumed.add(`${a.from.toLowerCase()}:${a.nonce.toLowerCase()}`);
  const consumed = await sc.facilitator.verify(body);
  assert.equal(consumed.invalidReason, 'invalid_transaction_state');

  const sc2 = scene();
  sc2.rpc.state.balances[sc2.payer.address.toLowerCase()] = 9_999n; // needs 10000
  const poor = await sc2.facilitator.verify(sc2.payment());
  assert.equal(poor.invalidReason, 'insufficient_funds');
});

test('verify: malformed payloads never crash, always structured errors', async () => {
  const sc = scene();
  for (const body of [
    null, {}, { x402Version: 1 },
    { x402Version: 2, paymentPayload: {}, paymentRequirements: null },
    sc.payment({ tamper: (b) => { delete b.paymentPayload.payload.authorization; return b; } }),
    sc.payment({ tamper: (b) => { b.paymentPayload.payload.authorization.value = '1.5'; return b; } }),
    sc.payment({ tamper: (b) => { b.paymentPayload.payload.authorization.nonce = '0x1234'; return b; } }),
    sc.payment({ tamper: (b) => { b.paymentPayload.payload.signature = 'hello'; return b; } }),
  ]) {
    const out = await sc.facilitator.verify(body);
    assert.equal(out.isValid, false, `should reject: ${JSON.stringify(body).slice(0, 80)}`);
    assert.ok(out.invalidReason, 'has a reason');
  }
});

test('verify is read-only: no DB rows, no txs sent', async () => {
  const sc = scene();
  await sc.facilitator.verify(sc.payment());
  assert.equal(sc.store.db.prepare('SELECT COUNT(*) c FROM payments').get().c, 0);
  assert.equal(sc.rpc.sent.length, 0);
});

/* -------------------------------------------------------------- settle -- */

test('settle: happy path settles on-chain and persists everything', async () => {
  const sc = scene();
  const out = await sc.facilitator.settle(sc.payment());
  assert.equal(out.success, true);
  assert.equal(out.network, 'eip155:8453');
  assert.equal(out.payer, sc.payer.address);
  assert.equal(out.amount, '10000');
  assert.ok(out.transaction.startsWith('0x'));
  assert.equal(sc.rpc.sent.length, 1);

  const row = sc.store.getByPaymentId(sc.store.db.prepare('SELECT payment_id FROM payments').get().payment_id);
  assert.equal(row.status, 'settled');
  assert.equal(row.settlement_tx_hash, out.transaction);
  assert.equal(row.amount, '10000');
  assert.equal(row.network, 'eip155:8453');
  assert.ok(BigInt(row.gas_spent_wei) > 0n, 'gas accounted (incl. l1Fee)');

  // metrics moved
  const text = sc.metrics.render();
  assert.match(text, /x402_settlements_total 1/);
  assert.match(text, /x402_revenue_usdc\{product="\/x402\/test"\} 0\.01/);
  assert.match(text, /x402_gas_spent_wei \d+/);
});

test('settle: concurrent duplicate settles -> exactly one succeeds, one broadcast', async () => {
  const sc = scene();
  const body = sc.payment();
  const [a, b] = await Promise.all([sc.facilitator.settle(body), sc.facilitator.settle(body)]);
  const succeeded = [a, b].filter((r) => r.success);
  const failed = [a, b].filter((r) => !r.success);
  assert.equal(succeeded.length, 1, 'exactly one settle wins');
  assert.equal(failed.length, 1);
  assert.equal(failed[0].errorReason, 'invalid_transaction_state');
  assert.equal(sc.rpc.sent.length, 1, 'exactly one tx broadcast');
  assert.match(sc.metrics.render(), /x402_replays_rejected_total 1/);
});

test('settle: replay after settlement is rejected (chain nonce consumed)', async () => {
  const sc = scene();
  const body = sc.payment();
  const first = await sc.facilitator.settle(body);
  assert.equal(first.success, true);
  const replay = await sc.facilitator.settle(body);
  assert.equal(replay.success, false);
  assert.equal(replay.errorReason, 'invalid_transaction_state');
  assert.equal(sc.rpc.sent.length, 1);
});

test('settle: DB-level idempotency — settled row returns stored result, no re-send', async () => {
  // Chain says nonce unconsumed (consumeOnSend off) so the request passes
  // verify and hits the DB claim — which must answer with the stored truth.
  const sc = scene({ rpcOpts: { consumeOnSend: false } });
  const body = sc.payment();
  const first = await sc.facilitator.settle(body);
  assert.equal(first.success, true);
  const again = await sc.facilitator.settle(body);
  assert.equal(again.success, true, 'idempotent settle');
  assert.equal(again.transaction, first.transaction);
  assert.equal(sc.rpc.sent.length, 1, 'no second broadcast');
});

test('settle: reverted tx -> failed row, gas accounted, no revenue', async () => {
  const sc = scene({ rpcOpts: { revertOnChain: true } });
  const out = await sc.facilitator.settle(sc.payment());
  assert.equal(out.success, false);
  assert.equal(out.errorReason, 'invalid_transaction_state');
  const row = sc.store.db.prepare('SELECT * FROM payments').get();
  assert.equal(row.status, 'failed');
  assert.equal(sc.store.settledRevenueAtomic(), 0n);
  assert.ok(sc.store.gasSpentSince(0) > 0n, 'burned gas is accounted');
  assert.match(sc.metrics.render(), /x402_settlement_failures_total\{reason="invalid_transaction_state"\} 1/);
});

test('settle: receipt timeout leaves row submitting (recoverable), never rebroadcasts', async () => {
  const sc = scene({ rpcOpts: { receiptForSend: false, consumeOnSend: false } });
  const out = await sc.facilitator.settle(sc.payment());
  assert.equal(out.success, false);
  assert.equal(out.errorReason, 'unexpected_settle_error');
  assert.ok(out.transaction.startsWith('0x'), 'tx hash reported for reconciliation');
  const row = sc.store.db.prepare('SELECT * FROM payments').get();
  assert.equal(row.status, 'submitting', 'unknown outcome stays submitting');
  assert.equal(sc.rpc.sent.length, 1);
});

test('settle: gas cap and fee cap enforced before any broadcast', async () => {
  const overGas = scene({ rpcOpts: { estimateGas: 200_000n } }); // cap 150k
  const out1 = await overGas.facilitator.settle(overGas.payment());
  assert.equal(out1.success, false);
  assert.equal(overGas.rpc.sent.length, 0, 'nothing broadcast');
  assert.equal(overGas.store.db.prepare('SELECT status FROM payments').get().status, 'failed');

  const feeSpike = scene({ rpcOpts: { baseFee: 2_000_000_000n }, envOverrides: { X402_MAX_FEE_PER_GAS_WEI: '1000000000' } });
  const out2 = await feeSpike.facilitator.settle(feeSpike.payment());
  assert.equal(out2.success, false);
  assert.equal(feeSpike.rpc.sent.length, 0);

  // simulation revert (e.g. consumed nonce raced us) maps to invalid_transaction_state
  const sim = scene({ rpcOpts: { estimateGasError: 'execution reverted: FiatTokenV2: authorization is used' } });
  const out3 = await sim.facilitator.settle(sim.payment());
  assert.equal(out3.success, false);
  assert.equal(out3.errorReason, 'invalid_transaction_state');
  assert.equal(sim.rpc.sent.length, 0);
});

test('settle: daily gas budget circuit breaker refuses BEFORE claiming', async () => {
  const sc = scene({ envOverrides: { X402_DAILY_GAS_BUDGET_WEI: '1000' } });
  // preload spend beyond budget
  sc.store.db.prepare('INSERT INTO gas_spend (payment_id, tx_hash, spent_wei, created_at) VALUES (?,?,?,?)')
    .run('pay_old', '0xold', '5000', Math.floor(Date.now() / 1000) - 60);
  const body = sc.payment();
  const out = await sc.facilitator.settle(body);
  assert.equal(out.success, false);
  assert.equal(sc.rpc.sent.length, 0);
  // the authorization was NOT claimed — payer's money untouched, retry-able elsewhere
  assert.equal(sc.store.db.prepare('SELECT COUNT(*) c FROM payments').get().c, 0);
});

test('settle: failed-before-broadcast authorization retries and then settles', async () => {
  const sc = scene({ rpcOpts: { estimateGasError: 'boom' } });
  const body = sc.payment();
  const out1 = await sc.facilitator.settle(body);
  assert.equal(out1.success, false);
  const row1 = sc.store.db.prepare('SELECT status, raw_tx FROM payments').get();
  assert.equal(row1.status, 'failed');
  assert.equal(row1.raw_tx, null, 'nothing was ever signed');

  // transient cause fixed -> the SAME authorization must be retryable
  sc.rpc.opts.estimateGasError = null;
  const out2 = await sc.facilitator.settle(body);
  assert.equal(out2.success, true, `retry should settle: ${out2.errorReason || ''} ${out2.errorDetail || ''}`);
  assert.equal(sc.rpc.sent.length, 1);
  assert.equal(sc.store.db.prepare('SELECT COUNT(*) c FROM payments').get().c, 1, 'same row reused');
  assert.equal(sc.store.db.prepare('SELECT status FROM payments').get().status, 'settled');
});

/* ------------------------------------------------------ crash recovery -- */

test('recovery: submitting row with consumed authorization becomes settled', async () => {
  const sc = scene();
  const body = sc.payment();
  const a = sc.lastAuth;
  // simulate a crash: row stuck submitting, chain HAS the settlement
  sc.store.claim({
    paymentId: 'p_crash', authorizationHash: '0x' + 'e1'.repeat(32), payer: a.from,
    asset: sc.cfg.asset, network: sc.cfg.caip2, amount: 10_000n, resource: '/x402/test',
    expiresAt: 2_000_000_000, authNonce: a.nonce,
  });
  sc.store.markSubmitting('p_crash', { txHash: '0xdeadbeef', txNonce: 5, rawTx: '0x02aa' });
  sc.rpc.state.authConsumed.add(`${a.from.toLowerCase()}:${a.nonce.toLowerCase()}`);
  // receipt exists under a DIFFERENT hash than we stored -> found via logs
  sc.rpc.state.receipts['0xrealtx'] = {
    transactionHash: '0xrealtx', status: '0x1', blockNumber: '0x64',
    gasUsed: '0x15122', effectiveGasPrice: '0x5f5e64',
    logs: [{ address: sc.cfg.asset, topics: [require('../src/facilitator-evm/usdc').TOPICS.authorizationUsed, require('./evm-helpers').word(a.from), a.nonce], data: '0x' }],
  };

  const report = await sc.facilitator.recoverInFlight();
  assert.equal(report.settled, 1);
  const row = sc.store.getByPaymentId('p_crash');
  assert.equal(row.status, 'settled');
  assert.equal(row.settlement_tx_hash, '0xrealtx');
  void body;
});

test('recovery: unconsumed + dropped tx -> SAME raw bytes rebroadcast', async () => {
  const sc = scene();
  sc.store.claim({
    paymentId: 'p_drop', authorizationHash: '0x' + 'e2'.repeat(32), payer: sc.payer.address,
    asset: sc.cfg.asset, network: sc.cfg.caip2, amount: 10_000n, resource: '',
    expiresAt: Math.floor(Date.now() / 1000) + 3600, authNonce: '0x' + 'f1'.repeat(32),
  });
  sc.store.markSubmitting('p_drop', { txHash: '0xgone', txNonce: 9, rawTx: '0x02cafe' });
  const report = await sc.facilitator.recoverInFlight();
  assert.equal(report.rebroadcast, 1);
  assert.deepEqual(sc.rpc.sent, ['0x02cafe'], 'exact stored bytes, never a new tx');
  assert.equal(sc.store.getByPaymentId('p_drop').status, 'submitting', 'still awaiting outcome');
});

test('recovery: pending rows (claimed, never signed) fail and free the authorization', async () => {
  const sc = scene();
  const auth = '0x' + 'e3'.repeat(32);
  sc.store.claim({
    paymentId: 'p_pend', authorizationHash: auth, payer: sc.payer.address,
    asset: sc.cfg.asset, network: sc.cfg.caip2, amount: 10_000n, resource: '',
    expiresAt: 2_000_000_000, authNonce: '0x' + 'f2'.repeat(32),
  });
  const report = await sc.facilitator.recoverInFlight();
  assert.equal(report.failedPending, 1);
  const again = sc.store.claim({
    paymentId: 'p_pend2', authorizationHash: auth, payer: sc.payer.address,
    asset: sc.cfg.asset, network: sc.cfg.caip2, amount: 10_000n, resource: '',
    expiresAt: 2_000_000_000, authNonce: '0x' + 'f2'.repeat(32),
  });
  assert.equal(again.claimed, true, 'authorization usable again after crash-before-sign');
});

/* ------------------------------------------------------------- readyz -- */

test('readyz: green when all dependencies healthy', async () => {
  const sc = scene();
  const r = await sc.facilitator.readiness();
  assert.equal(r.ready, true, JSON.stringify(r.checks));
  assert.match(sc.metrics.render(), /x402_ready 1/);
});

test('readyz gates: rpc down / chain mismatch / domain mismatch / low gas / db down', async () => {
  const down = scene();
  down.rpc.call = async () => { throw new Error('ECONNREFUSED'); };
  assert.equal((await down.facilitator.readiness()).ready, false);

  const wrongChain = scene({ rpcOpts: { chainId: 1 } });
  const r2 = await wrongChain.facilitator.readiness();
  assert.equal(r2.ready, false);
  assert.match(String(r2.checks.chain_id), /mismatch/);

  const wrongDomain = scene({ rpcOpts: { domainSeparatorOverride: '0x' + '00'.repeat(32) } });
  const r3 = await wrongDomain.facilitator.readiness();
  assert.equal(r3.ready, false);
  assert.match(String(r3.checks.usdc_domain), /DOMAIN_SEPARATOR/);

  const broke = scene({ rpcOpts: { gasBalance: 1n } });
  const r4 = await broke.facilitator.readiness();
  assert.equal(r4.ready, false);
  assert.match(String(r4.checks.gas_balance), /low/);

  const dbDown = scene();
  dbDown.store.close();
  const r5 = await dbDown.facilitator.readiness();
  assert.equal(r5.ready, false);
  assert.match(String(r5.checks.db), /down/);
});

/* ---------------------------------------------------------- HTTP layer -- */

test('http: /verify /supported /metrics /healthz respond; oversized body 413', async () => {
  const sc = scene();
  const server = createEvmFacilitatorServer(sc.facilitator);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const v = await (await fetch(`${base}/verify`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(sc.payment()),
    })).json();
    assert.equal(v.isValid, true);
    assert.equal(v.payer, sc.payer.address);

    const sup = await (await fetch(`${base}/supported`)).json();
    assert.deepEqual(sup.kinds, [{ x402Version: 2, scheme: 'exact', network: 'eip155:8453' }]);
    assert.deepEqual(sup.signers, { 'eip155:*': [sc.signer.address] });
    assert.ok(Array.isArray(sup.extensions));

    const hz = await fetch(`${base}/healthz`);
    assert.equal(hz.status, 200);

    const met = await fetch(`${base}/metrics`);
    assert.equal(met.status, 200);
    assert.match(await met.text(), /x402_settlements_total/);

    const big = await fetch(`${base}/settle`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: '{"pad":"' + 'x'.repeat(70 * 1024) + '"}',
    });
    assert.equal(big.status, 413);

    const nf = await fetch(`${base}/nope`);
    assert.equal(nf.status, 404);
  } finally {
    server.close();
  }
});

test('http: response never leaks the private key or raw signatures', async () => {
  const sc = scene();
  const server = createEvmFacilitatorServer(sc.facilitator);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    for (const path of ['/supported', '/healthz', '/readyz', '/metrics']) {
      const text = await (await fetch(base + path)).text();
      assert.ok(!text.includes(evm.strip0x(evm.bytesToHex(sc.payer.priv))), `${path} leaks payer key`);
      assert.ok(!/private/i.test(text) || !/[0-9a-f]{64}/.test(text), `${path} suspicious`);
    }
  } finally {
    server.close();
  }
});
