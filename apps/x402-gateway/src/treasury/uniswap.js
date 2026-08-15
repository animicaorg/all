'use strict';
/**
 * Uniswap v3 (SwapRouter02 + QuoterV2) and ERC-20 calldata for the treasury
 * "sip". Pure encoding/decoding — no I/O, no state, no keys. Every address,
 * selector and revert string below came from a live recon on Base mainnet
 * (2026-08-15, block ~49,991,642, chainId 8453) cross-checked on two
 * independent RPC providers plus an independent explorer's verified source;
 * the §4 multicall payload was executed as an eth_call/eth_estimateGas
 * state-override simulation before a line of this file was written. The
 * selectors are recomputed from their signatures by the test suite, and the
 * addresses are re-checksummed there, so a transcription slip fails CI rather
 * than a $5 swap.
 *
 * Three layout facts that silently ruin a swap if you get them wrong:
 *
 *   1. QuoterV2's struct puts `amountIn` BEFORE `fee`; SwapRouter02's struct
 *      puts `fee` third and `amountIn` fifth. They are NOT the same order.
 *   2. `unwrapWETH9` sends the router's ENTIRE WETH balance to `recipient`
 *      and does NOT honour the MSG_SENDER/ADDRESS_THIS sentinels — passing
 *      address(1) sends the ETH to the ecrecover precompile, which accepts it
 *      and does not revert. The recipient must be a real, checksummed EOA.
 *   3. `amountIn == 0` in exactInputSingle means CONTRACT_BALANCE (swap the
 *      router's whole balance), not "no-op".
 *
 * The router holds nothing at rest and anything left there is claimable by
 * anyone (`refundETH`/`sweepToken`), so the sip is one atomic multicall:
 * swap USDC -> WETH to the router, unwrap the router's WETH to the
 * facilitator. Either both legs land or the transaction reverts and no USDC
 * moves.
 */

const evm = require('../facilitator-evm/evm');

/* ------------------------------------------------------------ selectors -- */

const SELECTORS = {
  // QuoterV2
  quoteExactInputSingle: '0xc6a5026a', // quoteExactInputSingle((address,address,uint256,uint24,uint160))
  // SwapRouter02
  exactInputSingle: '0x04e45aaf', // exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))
  unwrapWETH9: '0x49404b7c', // unwrapWETH9(uint256,address)
  multicall: '0xac9650d8', // multicall(bytes[])
  multicallDeadline: '0x5ae401dc', // multicall(uint256,bytes[])
  // ERC-20 / FiatToken
  approve: '0x095ea7b3',
  allowance: '0xdd62ed3e',
  transfer: '0xa9059cbb',
  balanceOf: '0x70a08231',
  paused: '0x5c975abb',
  isBlacklisted: '0xfe575a87',
};

const TOPICS = {
  // Withdrawal(address indexed src, uint256 wad) — WETH9's unwrap event
  withdrawal: '0x7fcf532c15f0a6db0bd6d0e038bea71d30d808c7d98cb3bf7268a95bf5081b65',
  // Transfer(address indexed from, address indexed to, uint256 value)
  transfer: '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
};

/** Router recipient sentinels — legal in swap legs, POISON in unwrapWETH9. */
const RECIPIENT_SENTINELS = {
  MSG_SENDER: '0x0000000000000000000000000000000000000001',
  ADDRESS_THIS: '0x0000000000000000000000000000000000000002',
};

/* ------------------------------------------------------------ contracts -- */

/**
 * Per-network contract set. Only networks with a LIVE-VERIFIED set appear
 * here: the treasury refuses to run anywhere else rather than guess an
 * address for a swap that spends real revenue. base-sepolia is deliberately
 * absent — there is no verified router/quoter/pool set for it in the recon,
 * and a testnet sip buys nothing.
 */
const CONTRACTS = {
  base: {
    chainId: 8453,
    usdc: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
    weth9: '0x4200000000000000000000000000000000000006',
    factory: '0x33128a8fC17869897dcE68Ed026d694621f6FDfD',
    swapRouter02: '0x2626664c2603336E57B271c5C0b26F421741e481',
    quoterV2: '0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a',
    // Fee tiers whose USDC/WETH pool was verified to exist AND to quote
    // sanely for a $0.50-$5 sip. 10000 (1%) exists but its fee alone eats
    // 1% of the sip, so it is not allowlisted at all.
    allowedPoolFees: [100, 500, 3000],
    // 500 is the deepest pool (2.7M USDC / 3,880 WETH in range) and quotes
    // stable from $0.50 to $1,000; 100 is marginally cheaper but ~26x
    // thinner. Quoting both and taking the better fill costs two eth_calls.
    defaultPoolFees: [500, 100],
  },
};

function contractsFor(network) {
  const c = CONTRACTS[network];
  if (!c) {
    throw new Error(
      `x402 treasury has no live-verified Uniswap v3 contract set for network ${JSON.stringify(network)} `
      + `(supported: ${Object.keys(CONTRACTS).join(', ')}); refusing to swap against guessed addresses`
    );
  }
  return c;
}

/* -------------------------------------------------------------- encoding -- */

function addrWord(addr) {
  return '000000000000000000000000' + evm.strip0x(String(addr)).toLowerCase();
}

function uintWord(v) {
  if (typeof v !== 'bigint') throw new Error('uintWord expects a BigInt (money is never a Number here)');
  return Buffer.from(evm.bigintToWord(v)).toString('hex');
}

/** Pad a hex body (no 0x) to a whole number of 32-byte words. */
function padWords(bodyHex) {
  const rem = bodyHex.length % 64;
  return rem === 0 ? bodyHex : bodyHex + '0'.repeat(64 - rem);
}

/**
 * QuoterV2.quoteExactInputSingle. NOTE the struct order: tokenIn, tokenOut,
 * amountIn, fee, sqrtPriceLimitX96 (amountIn BEFORE fee). Call it with plain
 * eth_call — it is `external returns`, not `view` (it performs a real
 * pool.swap internally and catches the revert), so it must never be
 * STATICCALLed from a contract.
 */
function quoteExactInputSingleCalldata({ tokenIn, tokenOut, amountIn, fee, sqrtPriceLimitX96 = 0n }) {
  if (typeof amountIn !== 'bigint' || amountIn <= 0n) throw new Error('quote amountIn must be a positive BigInt');
  return SELECTORS.quoteExactInputSingle
    + addrWord(tokenIn)
    + addrWord(tokenOut)
    + uintWord(amountIn)
    + uintWord(BigInt(fee))
    + uintWord(BigInt(sqrtPriceLimitX96));
}

/** (amountOut, sqrtPriceX96After, initializedTicksCrossed, gasEstimate) */
function decodeQuoteResult(hex) {
  const body = evm.strip0x(String(hex));
  if (body.length < 64 * 4) throw new Error(`quoter returned ${body.length / 2} bytes, expected 4 words`);
  const w = (i) => BigInt('0x' + body.slice(i * 64, (i + 1) * 64));
  return {
    amountOut: w(0),
    sqrtPriceX96After: w(1),
    initializedTicksCrossed: Number(w(2)),
    gasEstimate: w(3),
  };
}

/**
 * SwapRouter02.exactInputSingle. Struct order: tokenIn, tokenOut, fee,
 * recipient, amountIn, amountOutMinimum, sqrtPriceLimitX96.
 * `amountOutMinimum` is the slippage guard (`Too little received`).
 */
function exactInputSingleCalldata({ tokenIn, tokenOut, fee, recipient, amountIn, amountOutMinimum, sqrtPriceLimitX96 = 0n }) {
  if (typeof amountIn !== 'bigint' || amountIn <= 0n) {
    // amountIn == 0 is the router's CONTRACT_BALANCE flag, never a no-op.
    throw new Error('exactInputSingle amountIn must be a positive BigInt (0 means CONTRACT_BALANCE)');
  }
  if (typeof amountOutMinimum !== 'bigint' || amountOutMinimum <= 0n) {
    throw new Error('exactInputSingle amountOutMinimum must be a positive BigInt (0 disables slippage protection)');
  }
  return SELECTORS.exactInputSingle
    + addrWord(tokenIn)
    + addrWord(tokenOut)
    + uintWord(BigInt(fee))
    + addrWord(recipient)
    + uintWord(amountIn)
    + uintWord(amountOutMinimum)
    + uintWord(BigInt(sqrtPriceLimitX96));
}

/**
 * SwapRouter02.unwrapWETH9(amountMinimum, recipient). Sends the router's
 * WHOLE WETH balance to `recipient` (amountMinimum is only a floor check),
 * and does NOT substitute the router's address sentinels — so a sentinel
 * here is a permanent loss of funds. Refused loudly.
 */
function unwrapWeth9Calldata(amountMinimum, recipient) {
  if (typeof amountMinimum !== 'bigint' || amountMinimum <= 0n) {
    throw new Error('unwrapWETH9 amountMinimum must be a positive BigInt');
  }
  const to = evm.validateAddress(recipient, 'unwrapWETH9 recipient');
  for (const [name, sentinel] of Object.entries(RECIPIENT_SENTINELS)) {
    if (evm.addressEquals(to, sentinel)) {
      throw new Error(
        `unwrapWETH9 recipient must be a real address, got the ${name} sentinel ${sentinel}: `
        + 'unwrapWETH9 does not substitute sentinels, so the ETH would be sent to a precompile and lost'
      );
    }
  }
  if (/^0x0{40}$/i.test(to)) throw new Error('unwrapWETH9 recipient must not be the zero address');
  return SELECTORS.unwrapWETH9 + uintWord(amountMinimum) + addrWord(to);
}

/** ABI-encode a `bytes[]` (length, per-element offsets, padded payloads). */
function encodeBytesArray(items) {
  const bodies = items.map((h) => evm.strip0x(String(h)));
  for (const b of bodies) {
    if (b.length % 2 !== 0 || !/^[0-9a-fA-F]*$/.test(b)) throw new Error('multicall element is not hex');
  }
  const head = [uintWord(BigInt(bodies.length))];
  // Element offsets are measured from the word immediately AFTER the length.
  let offset = 32 * bodies.length;
  const tail = [];
  for (const b of bodies) {
    head.push(uintWord(BigInt(offset)));
    const padded = padWords(b);
    tail.push(uintWord(BigInt(b.length / 2)) + padded);
    offset += 32 + padded.length / 2;
  }
  return head.join('') + tail.join('');
}

/** SwapRouter02.multicall(bytes[]) — kept for parity with the recon fixture. */
function multicallCalldata(items) {
  return SELECTORS.multicall + uintWord(32n) + encodeBytesArray(items);
}

/**
 * SwapRouter02.multicall(uint256 deadline, bytes[] data) — the form we send.
 * The 298 extra gas buys `checkDeadline`, the only thing that stops a stuck
 * sip from executing hours later against a stale amountOutMinimum.
 */
function multicallWithDeadlineCalldata(deadline, items) {
  if (typeof deadline !== 'bigint' || deadline <= 0n) throw new Error('multicall deadline must be a positive BigInt (unix seconds)');
  return SELECTORS.multicallDeadline + uintWord(deadline) + uintWord(64n) + encodeBytesArray(items);
}

/**
 * The complete sip transaction payload: swap USDC -> WETH into the router
 * itself, then unwrap the router's WETH to the facilitator EOA. Both legs
 * are delegatecalled from `multicall`, so msg.sender is preserved (the
 * EOA->router USDC allowance applies) and the whole thing is atomic.
 */
function sipCalldata({ usdc, weth9, fee, amountIn, amountOutMinimum, recipient, deadline }) {
  const swap = exactInputSingleCalldata({
    tokenIn: usdc,
    tokenOut: weth9,
    fee,
    recipient: RECIPIENT_SENTINELS.ADDRESS_THIS, // the router must hold the WETH to unwrap it
    amountIn,
    amountOutMinimum,
  });
  const unwrap = unwrapWeth9Calldata(amountOutMinimum, recipient);
  return multicallWithDeadlineCalldata(deadline, [swap, unwrap]);
}

/* ---------------------------------------------------------------- ERC-20 -- */

function approveCalldata(spender, amount) {
  if (typeof amount !== 'bigint' || amount < 0n) throw new Error('approve amount must be a non-negative BigInt');
  return SELECTORS.approve + addrWord(evm.validateAddress(spender, 'approve spender')) + uintWord(amount);
}

function allowanceCalldata(owner, spender) {
  return SELECTORS.allowance + addrWord(owner) + addrWord(spender);
}

function transferCalldata(to, amount) {
  if (typeof amount !== 'bigint' || amount <= 0n) throw new Error('transfer amount must be a positive BigInt');
  return SELECTORS.transfer + addrWord(evm.validateAddress(to, 'transfer recipient')) + uintWord(amount);
}

function balanceOfCalldata(addr) {
  return SELECTORS.balanceOf + addrWord(addr);
}

function pausedCalldata() {
  return SELECTORS.paused;
}

function isBlacklistedCalldata(addr) {
  return SELECTORS.isBlacklisted + addrWord(addr);
}

function decodeUint(hex) {
  const body = evm.strip0x(String(hex));
  if (body.length === 0) throw new Error('empty return data');
  return BigInt('0x' + body.slice(0, 64));
}

function decodeBool(hex) {
  return decodeUint(hex) !== 0n;
}

/* ---------------------------------------------------------------- events -- */

function topicIsAddress(topicWord, addr) {
  if (typeof topicWord !== 'string' || !evm.isHex(topicWord, 32)) return false;
  return '0x' + evm.strip0x(topicWord).slice(24).toLowerCase() === String(addr).toLowerCase();
}

/**
 * The ETH a sip actually delivered, straight from chain truth: WETH9's
 * `Withdrawal(src=router, wad)` in our own receipt. Returns the wad (BigInt)
 * or null when the log is absent. Balance polling is NOT a substitute —
 * eth_getBalance right after a receipt can still read the pre-tx value.
 */
function findWethWithdrawal(receipt, { weth9, router }) {
  const logs = Array.isArray(receipt && receipt.logs) ? receipt.logs : [];
  for (const l of logs) {
    if (!evm.addressEquals(l.address, weth9)) continue;
    if (!Array.isArray(l.topics) || l.topics.length < 2) continue;
    if (l.topics[0] !== TOPICS.withdrawal) continue;
    if (router && !topicIsAddress(l.topics[1], router)) continue;
    try {
      return decodeUint(l.data);
    } catch (e) {
      return null;
    }
  }
  return null;
}

/**
 * An ERC-20 Transfer(from, to, value) log. `from`/`to`/`value` are each
 * optional — undefined means "don't care", which is how the sip checks that
 * the USDC left our wallet without needing to know which pool it went to.
 */
function hasTransfer(receipt, { token, from, to, value }) {
  const logs = Array.isArray(receipt && receipt.logs) ? receipt.logs : [];
  return logs.some((l) =>
    evm.addressEquals(l.address, token) &&
    Array.isArray(l.topics) &&
    l.topics.length >= 3 &&
    l.topics[0] === TOPICS.transfer &&
    (from === undefined || topicIsAddress(l.topics[1], from)) &&
    (to === undefined || topicIsAddress(l.topics[2], to)) &&
    (value === undefined || (() => { try { return decodeUint(l.data) === value; } catch (e) { return false; } })())
  );
}

/* --------------------------------------------------------- revert classes -- */

/**
 * Map a revert/estimate error onto a POLICY class. The classes differ in
 * what they mean for the next attempt, which is the whole point:
 *
 *   price_moved  the quote went stale between quoting and executing. Costs
 *                nothing (the tx reverts atomically), retry next tick with a
 *                fresh quote — NOT a strike, or normal market noise would
 *                disable sipping.
 *   allowance    STF: the USDC transferFrom failed. Our approve did not land
 *                or was insufficient — a bug in our own flow. Strike.
 *   recipient    STE: the ETH recipient rejected the transfer. The recipient
 *                is our own configured address, so this is a hard
 *                misconfiguration: disable sipping immediately, do not spend
 *                two more gas-burning attempts proving it.
 *   pool         AS / zero-amount pool errors. Strike.
 *   unknown      anything else. Strike.
 */
function classifyRevert(message) {
  const m = String(message || '');
  if (/Too little received|Insufficient WETH9|Transaction too old|deadline/i.test(m)) {
    return { class: 'price_moved', strike: false, hardDisable: false };
  }
  if (/\bSTF\b|SafeTransferFrom|transfer amount exceeds allowance|ERC20: insufficient allowance/i.test(m)) {
    return { class: 'allowance', strike: true, hardDisable: false };
  }
  if (/\bSTE\b/.test(m)) {
    return { class: 'recipient', strike: true, hardDisable: true };
  }
  if (/\bAS\b|amountSpecified|LOK|SPL/.test(m)) {
    return { class: 'pool', strike: true, hardDisable: false };
  }
  return { class: 'unknown', strike: true, hardDisable: false };
}

module.exports = {
  SELECTORS,
  TOPICS,
  RECIPIENT_SENTINELS,
  CONTRACTS,
  contractsFor,
  quoteExactInputSingleCalldata,
  decodeQuoteResult,
  exactInputSingleCalldata,
  unwrapWeth9Calldata,
  encodeBytesArray,
  multicallCalldata,
  multicallWithDeadlineCalldata,
  sipCalldata,
  approveCalldata,
  allowanceCalldata,
  transferCalldata,
  balanceOfCalldata,
  pausedCalldata,
  isBlacklistedCalldata,
  decodeUint,
  decodeBool,
  findWethWithdrawal,
  hasTransfer,
  classifyRevert,
};
