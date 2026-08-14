'use strict';
/**
 * Test fixtures: a hand-rolled v0 Solana transaction builder (mirrors what
 * @x402/svm's ExactSvmScheme client produces: fee payer slot left unsigned,
 * authority signed) and a mock Solana RPC so no test ever touches a network.
 */

const crypto = require('node:crypto');
const sol = require('../src/solana');

/** Deterministic-ish keypair for tests. */
function kp() {
  return sol.keypairFromSeed(crypto.randomBytes(32));
}

/** Random base58 32-byte "address" (no known secret — fine for accounts). */
function addr() {
  return sol.base58Encode(crypto.randomBytes(32));
}

/**
 * Build a base64 partially-signed v0 transaction carrying one TransferChecked
 * (+ optional compute budget / memo / extra instructions).
 *
 * Accounts (static keys, no lookups):
 *   0 feePayer (signer, writable)      — signature left as 64 zero bytes
 *   1 authority (signer, writable)     — signs the message
 *   2 source (writable)
 *   3 destination (writable)
 *   4 mint (readonly)
 *   5 token program (readonly)
 *   [6 extra program ids...]
 */
function buildTransferTx({
  feePayer, // base58 pubkey
  authority, // keypair from kp()
  source,
  destination,
  mint,
  amount, // bigint
  decimals = 9,
  tokenProgram = sol.TOKEN_PROGRAM_ID,
  blockhash = sol.base58Encode(crypto.randomBytes(32)),
  computeBudget = true,
  memo = null,
  extraProgram = null, // base58 program id to add a bogus instruction for
  extraProgramAccounts = [], // account indices for that instruction
  signAuthority = true,
  transferTag = 12,
}) {
  const keys = [feePayer, authority.publicKey, source, destination, mint, tokenProgram];
  const push = (k) => {
    const i = keys.indexOf(k);
    if (i >= 0) return i;
    keys.push(k);
    return keys.length - 1;
  };
  const ixs = [];

  if (computeBudget) {
    const cbIdx = push(sol.COMPUTE_BUDGET_PROGRAM_ID);
    const limit = Buffer.alloc(5);
    limit[0] = 2; // SetComputeUnitLimit
    limit.writeUInt32LE(200_000, 1);
    ixs.push({ programIdIndex: cbIdx, accounts: [], data: limit });
    const price = Buffer.alloc(9);
    price[0] = 3; // SetComputeUnitPrice
    price.writeBigUInt64LE(1_000n, 1); // 1000 micro-lamports/CU, under cap
    ixs.push({ programIdIndex: cbIdx, accounts: [], data: price });
  }

  const data = Buffer.alloc(10);
  data[0] = transferTag; // 12 = TransferChecked
  data.writeBigUInt64LE(amount, 1);
  data[9] = decimals;
  ixs.push({ programIdIndex: 5, accounts: [2, 4, 3, 1], data });

  if (memo !== null) {
    const memoIdx = push(sol.MEMO_PROGRAM_ID);
    ixs.push({ programIdIndex: memoIdx, accounts: [], data: Buffer.from(memo, 'utf8') });
  }
  if (extraProgram) {
    const exIdx = push(extraProgram);
    ixs.push({ programIdIndex: exIdx, accounts: extraProgramAccounts, data: Buffer.from([9, 9, 9]) });
  }

  const numRequiredSignatures = 2;
  const numReadonlyUnsigned = keys.length - 4; // mint, programs
  const parts = [
    Buffer.from([0x80]), // v0 prefix
    Buffer.from([numRequiredSignatures, 0, numReadonlyUnsigned]),
    sol.shortvecEncode(keys.length),
    ...keys.map((k) => sol.decodePubkey(k)),
    sol.base58Decode(blockhash),
    sol.shortvecEncode(ixs.length),
  ];
  for (const ix of ixs) {
    parts.push(Buffer.from([ix.programIdIndex]));
    parts.push(sol.shortvecEncode(ix.accounts.length), Buffer.from(ix.accounts));
    parts.push(sol.shortvecEncode(ix.data.length), ix.data);
  }
  parts.push(sol.shortvecEncode(0)); // no address table lookups
  const messageBytes = Buffer.concat(parts);

  const signatures = [
    Buffer.alloc(64), // fee payer placeholder — the facilitator fills this
    signAuthority ? authority.sign(messageBytes) : Buffer.alloc(64),
  ];
  return sol.serializeTransaction(signatures, messageBytes);
}

/** Serialize an SPL token account the way getAccountInfo base64 returns it. */
function tokenAccountData({ mint, owner, amount }) {
  const buf = Buffer.alloc(165);
  sol.decodePubkey(mint).copy(buf, 0);
  sol.decodePubkey(owner).copy(buf, 32);
  buf.writeBigUInt64LE(amount, 64);
  buf[108] = 1; // state = initialized
  return buf.toString('base64');
}

/**
 * Mock Solana RPC. `accounts` maps pubkey -> {owner, data(b64)}. Records
 * every call; sendTransaction captures the wire tx for assertions.
 */
function mockRpc({ accounts = {}, sendError = null, confirm = true } = {}) {
  const calls = [];
  const sent = [];
  return {
    calls,
    sent,
    async call(method, params) {
      calls.push({ method, params });
      if (method === 'getAccountInfo') {
        const acc = accounts[params[0]];
        return { context: { slot: 1 }, value: acc ? { owner: acc.owner, data: [acc.data, 'base64'], lamports: 2_039_280, executable: false } : null };
      }
      if (method === 'sendTransaction') {
        if (sendError) throw new Error(sendError);
        sent.push(params[0]);
        const tx = sol.parseTransaction(params[0]);
        return sol.base58Encode(tx.signatures[0]);
      }
      if (method === 'getSignatureStatuses') {
        return { context: { slot: 2 }, value: [confirm ? { confirmationStatus: 'confirmed', err: null } : null] };
      }
      throw new Error(`mock rpc: unexpected method ${method}`);
    },
  };
}

/**
 * A complete, coherent wANM payment scene: facilitator keypair, payer,
 * treasury with its token account, funded source, and matching requirements.
 */
function scene({ priceAtomic = '5000000', decimals = 9 } = {}) {
  const feePayerKp = kp();
  const payerKp = kp();
  const mint = addr();
  const treasury = addr();
  const treasuryAta = addr();
  const sourceAta = addr();
  const network = 'solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1';

  const requirements = {
    scheme: 'exact',
    network,
    amount: priceAtomic,
    asset: mint,
    payTo: treasury,
    maxTimeoutSeconds: 60,
    extra: { feePayer: feePayerKp.publicKey },
  };

  const rpc = mockRpc({
    accounts: {
      [treasuryAta]: { owner: sol.TOKEN_PROGRAM_ID, data: tokenAccountData({ mint, owner: treasury, amount: 0n }) },
      [sourceAta]: { owner: sol.TOKEN_PROGRAM_ID, data: tokenAccountData({ mint, owner: payerKp.publicKey, amount: 10n ** 12n }) },
    },
  });

  const tx = (overrides = {}) => buildTransferTx(Object.assign({
    feePayer: feePayerKp.publicKey,
    authority: payerKp,
    source: sourceAta,
    destination: treasuryAta,
    mint,
    amount: BigInt(priceAtomic),
    decimals,
  }, overrides));

  const payload = (txB64) => ({
    x402Version: 2,
    accepted: requirements,
    payload: { transaction: txB64 },
  });

  return { feePayerKp, payerKp, mint, treasury, treasuryAta, sourceAta, network, requirements, rpc, tx, payload };
}

module.exports = { kp, addr, buildTransferTx, tokenAccountData, mockRpc, scene };
