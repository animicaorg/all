'use strict';
/**
 * A mock Base chain for the treasury tests: USDC (balances, allowances,
 * paused/blacklist), WETH9, QuoterV2 and SwapRouter02, driven by the ACTUAL
 * SIGNED TRANSACTION BYTES.
 *
 * That last part matters. The mock RLP-decodes every raw transaction the
 * module broadcasts and derives its effects from the decoded `to`/`data` —
 * so an assertion like "a sweep can only ever pay the cold address" is made
 * against the bytes that would hit the chain, not against an intermediate
 * value the module happened to pass around.
 *
 * Nothing here talks to a network, and every key is generated in-process.
 */

const evm = require('../src/facilitator-evm/evm');
const uniswap = require('../src/treasury/uniswap');
const cfgMod = require('../src/config');
const { loadSigner } = require('../src/facilitator-evm/key');
const { createStore } = require('../src/store');
const { createTreasuryStore } = require('../src/treasury/store');
const { createTreasury } = require('../src/treasury/treasury');
const { createMetrics } = require('../src/metrics');
const { kp, fakeClock, quietLogger } = require('./evm-helpers');

const C = uniswap.CONTRACTS.base;

/* ------------------------------------------------------------------ rlp -- */

function decodeItem(b, i) {
  const p = b[i];
  if (p === undefined) throw new Error('rlp: truncated');
  if (p <= 0x7f) return [b.slice(i, i + 1), i + 1];
  if (p <= 0xb7) {
    const len = p - 0x80;
    return [b.slice(i + 1, i + 1 + len), i + 1 + len];
  }
  if (p <= 0xbf) {
    const ll = p - 0xb7;
    const len = Number('0x' + b.slice(i + 1, i + 1 + ll).toString('hex'));
    const s = i + 1 + ll;
    return [b.slice(s, s + len), s + len];
  }
  const isShortList = p <= 0xf7;
  const ll = isShortList ? 0 : p - 0xf7;
  const len = isShortList ? p - 0xc0 : Number('0x' + b.slice(i + 1, i + 1 + ll).toString('hex'));
  const start = i + 1 + ll;
  const end = start + len;
  const out = [];
  let j = start;
  while (j < end) {
    const [item, next] = decodeItem(b, j);
    out.push(item);
    j = next;
  }
  return [out, end];
}

/** EIP-1559 raw tx -> { chainId, nonce, gasLimit, to, value, data }. */
function decodeEip1559(rawHex) {
  const buf = Buffer.from(evm.strip0x(rawHex), 'hex');
  if (buf[0] !== 0x02) throw new Error('mock chain: only type-2 transactions are supported');
  const [fields] = decodeItem(buf.slice(1), 0);
  const num = (b) => (b.length === 0 ? 0n : BigInt('0x' + b.toString('hex')));
  return {
    chainId: num(fields[0]),
    nonce: num(fields[1]),
    maxPriorityFeePerGas: num(fields[2]),
    maxFeePerGas: num(fields[3]),
    gasLimit: num(fields[4]),
    to: '0x' + fields[5].toString('hex'),
    value: num(fields[6]),
    data: '0x' + fields[7].toString('hex'),
  };
}

/* -------------------------------------------------------------- decoding -- */

function words(dataHex, from4 = true) {
  const body = evm.strip0x(dataHex).slice(from4 ? 8 : 0);
  const out = [];
  for (let i = 0; i + 64 <= body.length; i += 64) out.push(body.slice(i, i + 64));
  return out;
}

const wordToAddr = (w) => '0x' + w.slice(24);
const wordToBig = (w) => BigInt('0x' + w);

/** Pull the exactInputSingle + unwrapWETH9 legs out of a multicall payload. */
function decodeSipCalldata(dataHex) {
  const body = evm.strip0x(dataHex);
  const selector = '0x' + body.slice(0, 8);
  let cursor = 8;
  let deadline = null;
  if (selector === uniswap.SELECTORS.multicallDeadline) {
    deadline = BigInt('0x' + body.slice(8, 72));
    cursor = 72 + 64; // deadline word + offset word
  } else if (selector === uniswap.SELECTORS.multicall) {
    cursor = 8 + 64; // offset word
  } else {
    throw new Error(`mock router: unexpected selector ${selector}`);
  }
  const arrayStart = cursor;
  const n = Number(BigInt('0x' + body.slice(arrayStart, arrayStart + 64)));
  const offsetsStart = arrayStart + 64;
  const elements = [];
  for (let i = 0; i < n; i++) {
    const off = Number(BigInt('0x' + body.slice(offsetsStart + i * 64, offsetsStart + (i + 1) * 64)));
    const at = offsetsStart + off * 2;
    const len = Number(BigInt('0x' + body.slice(at, at + 64)));
    elements.push('0x' + body.slice(at + 64, at + 64 + len * 2));
  }
  const out = { deadline, swap: null, unwrap: null, elements };
  for (const el of elements) {
    const s = '0x' + evm.strip0x(el).slice(0, 8);
    if (s === uniswap.SELECTORS.exactInputSingle) {
      const w = words(el);
      out.swap = {
        tokenIn: wordToAddr(w[0]),
        tokenOut: wordToAddr(w[1]),
        fee: Number(wordToBig(w[2])),
        recipient: wordToAddr(w[3]),
        amountIn: wordToBig(w[4]),
        amountOutMinimum: wordToBig(w[5]),
      };
    } else if (s === uniswap.SELECTORS.unwrapWETH9) {
      const w = words(el);
      out.unwrap = { amountMinimum: wordToBig(w[0]), recipient: wordToAddr(w[1]) };
    }
  }
  return out;
}

/* ------------------------------------------------------------ mock chain -- */

/**
 * Quote model: linear in amountIn with a per-fee-tier rate taken from the
 * live recon ($5.00 -> 2,658,950,900,358,165 wei at fee 500). Fee 100 quotes
 * marginally better, fee 3000 marginally worse — exactly the live ordering,
 * so "quote both tiers and take the best fill" is a testable behaviour.
 */
const QUOTE_NUMER = { 100: 2659293473602823n, 500: 2658950900358165n, 3000: 2652467823860696n };
const QUOTE_DENOM = 5_000_000n;

function createMockChain(opts = {}) {
  const state = {
    eth: new Map(),        // address(lower) -> wei
    usdc: new Map(),       // address(lower) -> atomic
    allowance: new Map(),  // `${owner}:${spender}` -> atomic
    paused: false,
    blacklisted: new Set(),
    receipts: {},
    txs: {},
    blockNumber: 1000n,
    nonce: 5,
    baseFee: 5_000_000n,
    priorityFee: 1_000_000n,
    // failure injection
    revertSwapWith: null,     // string -> estimateGas throws / tx reverts
    revertSweepWith: null,
    revertApproveWith: null,
    failAtEstimate: true,     // false => the revert happens on-chain instead
    sendError: null,
    dropReceipt: false,
    omitWithdrawalLog: false,
    quoteError: null,
    ...opts,
  };
  const calls = [];
  const sent = [];

  const lc = (a) => String(a).toLowerCase();
  const getEth = (a) => state.eth.get(lc(a)) || 0n;
  const setEth = (a, v) => state.eth.set(lc(a), v);
  const getUsdc = (a) => state.usdc.get(lc(a)) || 0n;
  const setUsdc = (a, v) => state.usdc.set(lc(a), v);

  function quoteOut(amountIn, fee) {
    const numer = QUOTE_NUMER[fee];
    if (numer === undefined) throw new Error('mock quoter: no pool for that fee tier');
    return (amountIn * numer) / QUOTE_DENOM;
  }

  function receipt(txHash, { status = '0x1', logs = [] } = {}) {
    return {
      transactionHash: txHash,
      status,
      blockNumber: '0x' + state.blockNumber.toString(16),
      gasUsed: '0x28649', // 165,449
      effectiveGasPrice: '0x5b8d80', // 6,000,000 wei
      l1Fee: '0x217e7a1d', // OP-stack data fee
      logs,
    };
  }

  const topicAddr = (a) => '0x' + '0'.repeat(24) + evm.strip0x(a).toLowerCase();
  const dataWord = (v) => '0x' + v.toString(16).padStart(64, '0');

  /**
   * Apply a decoded tx. Returns { logs } or throws a revert Error.
   * `commit: false` runs every check but mutates nothing — that is what
   * eth_estimateGas does on a real node, and a mock that mutated during
   * simulation would silently consume the allowance the real send needs.
   */
  function execute(tx, from, { commit = true } = {}) {
    const to = lc(tx.to);
    const data = tx.data;
    const sel = '0x' + evm.strip0x(data).slice(0, 8);

    if (to === lc(C.usdc)) {
      if (sel === uniswap.SELECTORS.approve) {
        if (state.revertApproveWith) throw new Error(state.revertApproveWith);
        const w = words(data);
        if (commit) state.allowance.set(`${lc(from)}:${lc(wordToAddr(w[0]))}`, wordToBig(w[1]));
        return { logs: [] };
      }
      if (sel === uniswap.SELECTORS.transfer) {
        if (state.revertSweepWith) throw new Error(state.revertSweepWith);
        const w = words(data);
        const dest = wordToAddr(w[0]);
        const value = wordToBig(w[1]);
        if (getUsdc(from) < value) throw new Error('ERC20: transfer amount exceeds balance');
        if (commit) {
          setUsdc(from, getUsdc(from) - value);
          setUsdc(dest, getUsdc(dest) + value);
        }
        return {
          logs: [{ address: C.usdc, topics: [uniswap.TOPICS.transfer, topicAddr(from), topicAddr(dest)], data: dataWord(value) }],
        };
      }
      throw new Error(`mock usdc: unexpected selector ${sel}`);
    }

    if (to === lc(C.swapRouter02)) {
      if (state.revertSwapWith) throw new Error(state.revertSwapWith);
      const call = decodeSipCalldata(data);
      if (!call.swap || !call.unwrap) throw new Error('mock router: multicall must carry exactInputSingle + unwrapWETH9');
      const { amountIn, amountOutMinimum, fee, recipient } = call.swap;
      if (!evm.addressEquals(recipient, uniswap.RECIPIENT_SENTINELS.ADDRESS_THIS)) {
        // The WETH would go to the EOA, leaving the router with nothing to
        // unwrap — the live chain answers "Insufficient WETH9".
        throw new Error('Insufficient WETH9');
      }
      const allowed = state.allowance.get(`${lc(from)}:${lc(C.swapRouter02)}`) || 0n;
      if (allowed < amountIn) throw new Error('STF');
      if (getUsdc(from) < amountIn) throw new Error('STF');
      const out = quoteOut(amountIn, fee);
      if (out < amountOutMinimum) throw new Error('Too little received');
      if (call.unwrap.amountMinimum > out) throw new Error('Insufficient WETH9');
      if (commit) {
        state.allowance.set(`${lc(from)}:${lc(C.swapRouter02)}`, allowed - amountIn);
        setUsdc(from, getUsdc(from) - amountIn);
        setEth(call.unwrap.recipient, getEth(call.unwrap.recipient) + out);
      }
      const logs = [
        { address: C.usdc, topics: [uniswap.TOPICS.transfer, topicAddr(from), topicAddr('0xd0b53d9277642d899df5c87a3966a349a798f224')], data: dataWord(amountIn) },
      ];
      if (!state.omitWithdrawalLog) {
        logs.push({ address: C.weth9, topics: [uniswap.TOPICS.withdrawal, topicAddr(C.swapRouter02)], data: dataWord(out) });
      }
      return { logs, amountOut: out };
    }

    throw new Error(`mock chain: no contract at ${tx.to}`);
  }

  const rpc = {
    calls,
    sent,
    state,
    decodeSipCalldata,
    decodeEip1559,
    setEth,
    getEth,
    setUsdc,
    getUsdc,
    quoteOut,
    async call(method, params = []) {
      calls.push({ method, params });
      switch (method) {
        case 'eth_chainId':
          return '0x2105';
        case 'eth_blockNumber':
          return '0x' + (state.blockNumber + 100n).toString(16);
        case 'eth_getBlockByNumber':
          return { number: '0x' + state.blockNumber.toString(16), baseFeePerGas: '0x' + state.baseFee.toString(16) };
        case 'eth_maxPriorityFeePerGas':
          return '0x' + state.priorityFee.toString(16);
        case 'eth_getTransactionCount':
          return '0x' + BigInt(state.nonce).toString(16);
        case 'eth_getBalance':
          return '0x' + getEth(params[0]).toString(16);
        case 'eth_estimateGas': {
          const { from, to, data } = params[0];
          if (state.failAtEstimate) execute({ to, data }, from, { commit: false }); // throws on revert
          // Live-measured gas for each shape (recon §7): approve 56,240 cold,
          // ERC-20 transfer ~63,000, swap+unwrap multicall 165,389.
          const sel = '0x' + evm.strip0x(String(data)).slice(0, 8);
          const gas = sel === uniswap.SELECTORS.approve ? 56_240n
            : sel === uniswap.SELECTORS.transfer ? 63_000n
              : 165_389n;
          return '0x' + (state.estimateGasOverride || gas).toString(16);
        }
        case 'eth_call': {
          const to = lc(params[0].to);
          const data = String(params[0].data);
          const sel = '0x' + evm.strip0x(data).slice(0, 8);
          if (to === lc(C.quoterV2)) {
            if (state.quoteError) throw new Error(state.quoteError);
            const w = words(data);
            const amountIn = wordToBig(w[2]);
            const fee = Number(wordToBig(w[3]));
            // quoteInflateBps makes the quoter LIE (or the price move between
            // quoting and executing) — the pool still fills at the true rate,
            // so amountOutMinimum is what has to catch it.
            const out = (quoteOut(amountIn, fee) * (10_000n + BigInt(state.quoteInflateBps || 0))) / 10_000n;
            return '0x' + out.toString(16).padStart(64, '0')
              + '0'.repeat(64) + '1'.padStart(64, '0') + (86263n).toString(16).padStart(64, '0');
          }
          if (to === lc(C.usdc)) {
            if (sel === '0x3644e515') { // DOMAIN_SEPARATOR() — the facilitator's startup self-check
              return cfgMod.EVM_NETWORKS.base.usdc.domainSeparator;
            }
            if (sel === uniswap.SELECTORS.balanceOf) {
              return '0x' + getUsdc(wordToAddr(words(data)[0])).toString(16).padStart(64, '0');
            }
            if (sel === uniswap.SELECTORS.allowance) {
              const w = words(data);
              const a = state.allowance.get(`${lc(wordToAddr(w[0]))}:${lc(wordToAddr(w[1]))}`) || 0n;
              return '0x' + a.toString(16).padStart(64, '0');
            }
            if (sel === uniswap.SELECTORS.paused) {
              return '0x' + (state.paused ? '1' : '0').padStart(64, '0');
            }
            if (sel === uniswap.SELECTORS.isBlacklisted) {
              const who = wordToAddr(words(data)[0]);
              return '0x' + (state.blacklisted.has(lc(who)) ? '1' : '0').padStart(64, '0');
            }
          }
          throw new Error(`mock rpc: unexpected eth_call to ${params[0].to} selector ${sel}`);
        }
        case 'eth_sendRawTransaction': {
          if (state.sendError) {
            const e = new Error(typeof state.sendError === 'string' ? state.sendError : state.sendError.message);
            if (state.sendError && state.sendError.transport) e.transport = true;
            throw e;
          }
          const raw = params[0];
          const tx = decodeEip1559(raw);
          const from = rpc.senderOf ? rpc.senderOf(raw) : null;
          sent.push({ raw, tx, from });
          const hash = evm.bytesToHex(evm.keccak(evm.hexToBytes(raw)));
          state.txs[hash] = { hash };
          state.nonce = Number(tx.nonce) + 1;
          // Test hook: fires INSIDE the send, i.e. while the caller still
          // holds the shared submit lock. Used to prove a settlement queued
          // at that exact moment is not made to wait for the sip's receipt.
          if (typeof state.onSend === 'function') state.onSend({ tx, hash });
          if (!state.dropReceipt) {
            try {
              const res = execute(tx, from);
              state.receipts[hash] = receipt(hash, { logs: res.logs });
            } catch (e) {
              state.receipts[hash] = receipt(hash, { status: '0x0', logs: [] });
            }
          }
          return hash;
        }
        case 'eth_getTransactionReceipt':
          return state.receipts[params[0]] || null;
        case 'eth_getTransactionByHash':
          return state.txs[params[0]] || null;
        default:
          throw new Error(`mock rpc: unexpected method ${method}`);
      }
    },
  };
  return rpc;
}

/* ----------------------------------------------------------------- scene -- */

/**
 * A complete treasury scene. `singleWallet: true` (the default) makes
 * X402_SETTLEMENT_ADDRESS the facilitator's own address, which is the
 * operator's deployment and the one the module exists for.
 */
function treasuryScene({
  env = {},
  chain = {},
  eth = 10n ** 15n,      // 0.001 ETH — comfortably above the default floor
  usdc = 0n,
  singleWallet = true,
  enabled = true,
  storePath = ':memory:',
  withSubmitLock,
  // Receipt polling that yields the MACROTASK queue between attempts while
  // still driving the fake clock. Needed only by the isolation test, which
  // has to observe a settlement running while a sip is mid-poll.
  yieldingSleep = false,
} = {}) {
  const facilitatorKey = kp();
  const cold = kp().address;
  const separatePayTo = kp().address;

  const source = {
    X402_NETWORK: 'base',
    X402_RPC_URL: 'http://127.0.0.1:1/mock',
    X402_SETTLEMENT_ADDRESS: singleWallet ? facilitatorKey.address : separatePayTo,
    X402_FACILITATOR_PRIVATE_KEY: facilitatorKey.privHex,
    X402_RECEIPT_POLL_MS: '100',
    X402_TREASURY_ENABLED: enabled ? '1' : '0',
    X402_TREASURY_COLD_ADDRESS: cold,
    ...env,
  };
  const cfg = cfgMod.loadEvmFacilitatorConfig(source);
  const signer = loadSigner(cfg.privateKey);
  delete cfg.privateKey;

  const rpc = createMockChain(chain);
  // The mock needs to know who signed each tx; the only signer in play is
  // the facilitator, so recovering the sender is unnecessary ceremony.
  rpc.senderOf = () => signer.address;
  rpc.setEth(signer.address, eth);
  rpc.setUsdc(signer.address, usdc);

  const store = createStore(storePath);
  const clock = fakeClock();
  const tstore = createTreasuryStore(store.db, { now: clock.now });
  const metrics = createMetrics();
  const logs = [];
  const logger = {
    debug() {}, info(e, f) { logs.push({ level: 'info', event: e, fields: f }); },
    warn(e, f) { logs.push({ level: 'warn', event: e, fields: f }); },
    error(e, f) { logs.push({ level: 'error', event: e, fields: f }); },
    child() { return logger; },
  };

  const sleep = yieldingSleep
    ? (ms) => new Promise((r) => setTimeout(() => { clock.advance(ms); r(); }, 0))
    : clock.sleep;

  const treasury = createTreasury({
    cfg, rpc, tstore, signer, metrics, logger,
    withSubmitLock,
    now: clock.now,
    sleep,
  });

  return {
    cfg, signer, rpc, store, tstore, metrics, logger, logs, clock, treasury,
    cold,
    facilitator: signer.address,
    contracts: C,
    /** A full facilitator over the same DB/chain (its own treasury instance). */
    createFacilitator: () => require('../src/facilitator-evm/server').createEvmFacilitator({
      cfg, rpc, store, signer, metrics, logger, sleep: clock.sleep, now: clock.now,
    }),
    /** Convenience: current facilitator balances in the mock chain. */
    balances: () => ({ eth: rpc.getEth(signer.address), usdc: rpc.getUsdc(signer.address) }),
  };
}

module.exports = {
  createMockChain,
  treasuryScene,
  decodeEip1559,
  decodeSipCalldata,
  QUOTE_NUMER,
  QUOTE_DENOM,
  C,
};
