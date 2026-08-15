'use strict';
/**
 * EVM test fixtures: throwaway in-test keys (never persisted, never real),
 * an EIP-3009 payment-payload builder that signs exactly like a compliant
 * x402 client, and a mock EVM JSON-RPC so no test touches a network.
 */

const secp = require('@noble/secp256k1');

const cfgMod = require('../src/config');
const evm = require('../src/facilitator-evm/evm');
const usdc = require('../src/facilitator-evm/usdc');
const { loadSigner } = require('../src/facilitator-evm/key');
const { createStore } = require('../src/store');
const { createMetrics } = require('../src/metrics');
const { createEvmFacilitator } = require('../src/facilitator-evm/server');

/** Throwaway secp256k1 keypair (generated in-test, in-memory only). */
function kp() {
  const priv = secp.utils.randomSecretKey();
  return { priv, address: evm.privateKeyToAddress(priv), privHex: evm.bytesToHex(priv) };
}

function word(addr) {
  return '0x' + '0'.repeat(24) + evm.strip0x(addr).toLowerCase();
}

/** Ethereum wire signature (r||s||v hex) over a 32-byte digest. */
function ethSign(digest, priv) {
  const s = evm.signDigest(digest, priv);
  return '0x' + Buffer.from(s.rWord).toString('hex') + Buffer.from(s.sWord).toString('hex') + s.v.toString(16).padStart(2, '0');
}

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return quietLogger; } };

/** Deterministic fake clock: sleep() advances time, nothing really waits. */
function fakeClock() {
  let t = 1_000_000;
  return {
    now: () => t,
    sleep: async (ms) => { t += ms; },
    advance: (ms) => { t += ms; },
  };
}

/**
 * Mock EVM JSON-RPC. Dispatches eth_call by selector. `sent` records raw
 * txs; a successful send auto-generates a correct settlement receipt (and,
 * when consumeOnSend, flips authorizationState) so the happy path mirrors
 * the real chain's behavior.
 */
function mockEvmRpc(scene, optsIn = {}) {
  // Mutable options object (exposed as rpc.opts): tests flip failure modes
  // mid-test, e.g. break estimateGas, settle, fix it, settle again.
  const opts = {
    chainId: 8453,
    blockTimestamp: 1_700_000_000n,
    baseFee: 5_000_000n,
    priorityFee: 1_000_000n,
    estimateGas: 82_000n,
    estimateGasError: null,
    sendError: null,           // string => app error; {transport:true, message} => transport error
    consumeOnSend: true,
    receiptForSend: true,      // false => receipts stay null (timeout path)
    revertOnChain: false,      // receipt with status 0x0
    gasBalance: 10n ** 18n,
    domainSeparatorOverride: null,
    txCount: 7,
    ...optsIn,
  };
  const calls = [];
  const sent = [];
  const state = {
    balances: scene ? { [scene.payer.address.toLowerCase()]: 1_000_000_000n } : {},
    authConsumed: new Set(), // `${payer}:${nonce}` lowercase
    receipts: {},            // txHash -> receipt
    txs: {},                 // txHash -> tx object
    blockNumber: 1000n,
  };

  function makeReceipt(txHash, { status = '0x1' } = {}) {
    const a = scene.lastAuth;
    if (!a) {
      // No authorization in play (e.g. recovery rebroadcast of stored raw
      // bytes) — a bare success receipt with no logs.
      return { transactionHash: txHash, status, blockNumber: '0x' + state.blockNumber.toString(16), gasUsed: '0x15122', effectiveGasPrice: '0x5f5e64', logs: [] };
    }
    return {
      transactionHash: txHash,
      status,
      blockNumber: '0x' + state.blockNumber.toString(16),
      gasUsed: '0x15122', // 86306
      effectiveGasPrice: '0x5f5e64', // 6,250,020
      l1Fee: '0x6f7ceb00', // OP-stack data fee
      logs: status !== '0x1' ? [] : [
        {
          address: scene.cfg.asset,
          topics: [usdc.TOPICS.authorizationUsed, word(a.from), a.nonce],
          data: '0x',
        },
        {
          address: scene.cfg.asset,
          topics: [usdc.TOPICS.transfer, word(a.from), word(a.to)],
          data: '0x' + a.value.toString(16).padStart(64, '0'),
        },
      ],
    };
  }

  const rpc = {
    calls,
    sent,
    state,
    opts,
    async call(method, params = []) {
      calls.push({ method, params });
      switch (method) {
        case 'eth_chainId':
          return '0x' + BigInt(opts.chainId).toString(16);
        case 'eth_blockNumber':
          return '0x' + (state.blockNumber + 100n).toString(16); // confirmations satisfied
        case 'eth_getBlockByNumber':
          return {
            number: '0x' + state.blockNumber.toString(16),
            timestamp: '0x' + opts.blockTimestamp.toString(16),
            baseFeePerGas: '0x' + opts.baseFee.toString(16),
          };
        case 'eth_maxPriorityFeePerGas':
          return '0x' + opts.priorityFee.toString(16);
        case 'eth_getTransactionCount':
          return '0x' + BigInt(opts.txCount).toString(16);
        case 'eth_getBalance':
          return '0x' + opts.gasBalance.toString(16);
        case 'eth_estimateGas':
          if (opts.estimateGasError) throw new Error(opts.estimateGasError);
          return '0x' + opts.estimateGas.toString(16);
        case 'eth_call': {
          const data = String(params[0].data);
          if (data.startsWith(usdc.SELECTORS.domainSeparator)) {
            return opts.domainSeparatorOverride || scene.domainSepHex;
          }
          if (data.startsWith(usdc.SELECTORS.balanceOf)) {
            const addr = '0x' + data.slice(10 + 24, 10 + 64);
            const bal = state.balances[addr.toLowerCase()] ?? 0n;
            return '0x' + bal.toString(16).padStart(64, '0');
          }
          if (data.startsWith(usdc.SELECTORS.authorizationState)) {
            const authorizer = '0x' + data.slice(10 + 24, 10 + 64);
            const nonce = '0x' + data.slice(10 + 64, 10 + 128);
            const used = state.authConsumed.has(`${authorizer.toLowerCase()}:${nonce.toLowerCase()}`);
            return '0x' + (used ? '1' : '0').padStart(64, '0');
          }
          throw new Error(`mock rpc: unexpected eth_call selector ${data.slice(0, 10)}`);
        }
        case 'eth_sendRawTransaction': {
          if (opts.sendError) {
            const e = new Error(typeof opts.sendError === 'string' ? opts.sendError : opts.sendError.message);
            if (opts.sendError && opts.sendError.transport) e.transport = true;
            throw e;
          }
          sent.push(params[0]);
          const hash = evm.bytesToHex(evm.keccak(evm.hexToBytes(params[0])));
          state.txs[hash] = { hash };
          if (opts.receiptForSend) {
            state.receipts[hash] = makeReceipt(hash, { status: opts.revertOnChain ? '0x0' : '0x1' });
          }
          if (opts.consumeOnSend && !opts.revertOnChain && scene.lastAuth) {
            state.authConsumed.add(`${scene.lastAuth.from.toLowerCase()}:${scene.lastAuth.nonce.toLowerCase()}`);
          }
          return hash;
        }
        case 'eth_getTransactionReceipt':
          return state.receipts[params[0]] || null;
        case 'eth_getTransactionByHash':
          return state.txs[params[0]] || null;
        case 'eth_getLogs': {
          const out = [];
          for (const [hash, r] of Object.entries(state.receipts)) {
            for (const l of r.logs || []) {
              if (l.topics[0] === params[0].topics[0] &&
                  l.topics[1] === params[0].topics[1] &&
                  l.topics[2] === params[0].topics[2]) {
                out.push({ ...l, transactionHash: hash });
              }
            }
          }
          return out;
        }
        default:
          throw new Error(`mock rpc: unexpected method ${method}`);
      }
    },
  };
  return rpc;
}

/**
 * A complete coherent facilitator scene: strict config (exercises the real
 * loader), throwaway facilitator + payer keys, in-memory store, mock RPC,
 * fake clock, and a compliant payment-payload builder.
 */
function scene({ rpcOpts = {}, envOverrides = {}, storePath = ':memory:' } = {}) {
  const facilitatorKey = kp();
  const payer = kp();
  const payTo = kp().address; // settlement address (no key needed)

  const cfg = cfgMod.loadEvmFacilitatorConfig({
    X402_NETWORK: 'base',
    X402_RPC_URL: 'http://127.0.0.1:1/mock',
    X402_SETTLEMENT_ADDRESS: payTo,
    X402_FACILITATOR_PRIVATE_KEY: facilitatorKey.privHex,
    X402_RECEIPT_POLL_MS: '100',
    ...envOverrides,
  });
  const signer = loadSigner(cfg.privateKey);
  delete cfg.privateKey;

  const domainSepBytes = evm.domainSeparator({
    name: cfg.eip712.name, version: cfg.eip712.version, chainId: cfg.chainId, verifyingContract: cfg.asset,
  });

  const sc = {
    cfg,
    signer,
    payer,
    payTo,
    domainSepBytes,
    domainSepHex: evm.bytesToHex(domainSepBytes),
    lastAuth: null,
  };

  sc.rpc = mockEvmRpc(sc, { ...rpcOpts });
  sc.store = createStore(storePath);
  sc.metrics = createMetrics();
  sc.clock = fakeClock();
  sc.facilitator = createEvmFacilitator({
    cfg,
    rpc: sc.rpc,
    store: sc.store,
    signer,
    metrics: sc.metrics,
    logger: quietLogger,
    sleep: sc.clock.sleep,
    now: sc.clock.now,
  });

  /**
   * Build a spec-shaped /verify //settle body. Overrides poke individual
   * fields AFTER signing (tamper) or BEFORE (legitimate variation).
   */
  sc.payment = function payment({
    value = 10_000n,               // $0.01 at 6 decimals
    to = payTo,
    validAfter = 1_699_999_000n,   // relative to mock blockTimestamp 1.7e9
    validBefore = 1_700_003_600n,
    nonce = evm.bytesToHex(secp.etc.randomBytes(32)),
    signWithDomain = domainSepBytes,
    signWithKey = payer.priv,
    amount = String(value),
    network = cfg.caip2,
    asset = cfg.asset,
    reqPayTo = payTo,
    tamper = null,                 // (payload) => payload, applied after signing
  } = {}) {
    const auth = { from: payer.address, to, value, validAfter, validBefore, nonce: nonce.toLowerCase() };
    sc.lastAuth = auth;
    const digest = usdc.transferAuthDigest(signWithDomain, auth);
    const signature = ethSign(digest, signWithKey);
    const requirements = {
      scheme: 'exact',
      network,
      amount,
      asset,
      payTo: reqPayTo,
      maxTimeoutSeconds: 60,
      extra: { name: cfg.eip712.name, version: cfg.eip712.version },
    };
    let body = {
      x402Version: 2,
      paymentPayload: {
        x402Version: 2,
        resource: '/x402/test',
        accepted: requirements,
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
      },
      paymentRequirements: requirements,
    };
    if (tamper) body = tamper(body) || body;
    return body;
  };

  return sc;
}

module.exports = { kp, word, ethSign, fakeClock, mockEvmRpc, scene, quietLogger };
