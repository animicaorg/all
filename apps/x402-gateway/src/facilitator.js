'use strict';
/**
 * Self-facilitation server for the wANM lane: implements the facilitator HTTP
 * contract of x402 spec v2 §7 for the `exact` scheme on Solana (specs/
 * schemes/exact/scheme_exact_svm.md), talking plain Solana JSON-RPC.
 *
 *   POST /verify  {x402Version:2, paymentPayload, paymentRequirements}
 *                 -> {isValid, invalidReason?, payer?}        (read-only)
 *   POST /settle  same request -> {success, errorReason?, payer?,
 *                 transaction, network, amount?}
 *   GET  /supported -> {kinds:[{x402Version:2, scheme, network, extra}],
 *                 extensions:[], signers:{"<caip2 network>":[feePayer]}}
 *
 * Scheme mechanics: the server's accepts entry advertises asset=mint,
 * payTo=merchant owner wallet, extra.feePayer=this facilitator's sponsor
 * pubkey. The client builds a versioned tx whose fee payer is our sponsor,
 * partially signs it (authority only), and sends it base64 in
 * payload.transaction. /verify statically checks the layout; /settle signs as
 * fee payer and submits.
 *
 * Deviation, documented: the spec's static check derives ATA(payTo, asset)
 * to pin the destination. Deriving an ATA offline needs an ed25519
 * on-curve check; rather than hand-roll curve math in a payments path, we
 * verify the destination via RPC — the account must be a token account whose
 * owner is payTo and whose mint is the asset. Strictly stronger than the ATA
 * shape check for accounts that exist, and the transfer fails on-chain for
 * ones that don't (we do not create ATAs for payers).
 *
 * Replay: /settle marks sha256(messageBytes) used BEFORE broadcasting, and
 * the mark survives a failed send on purpose — the send may have landed even
 * when our HTTP call to the RPC did not come back. A stuck-but-honest payer
 * retries with a fresh blockhash, which is a new message hash. In-memory
 * store: acceptable for a scaffold, documented as a TODO for anything real
 * (restart forgets marks; the chain itself still rejects a literal re-send
 * of an already-landed tx, so the residual risk is a double-settle race
 * across restarts, not a double-spend).
 */

const http = require('node:http');

const cfgMod = require('./config');
const sol = require('./solana');

const SCHEME = 'exact';
const MAX_COMPUTE_UNIT_PRICE_MICRO_LAMPORTS = 5_000_000n; // 5 lamports/CU, per spec static check

class ReplayStore {
  constructor({ ttlMs = 30 * 60 * 1000, now = Date.now } = {}) {
    this.seen = new Map(); // hash -> expiresAt
    this.ttlMs = ttlMs;
    this.now = now;
  }
  has(key) {
    this.gc();
    return this.seen.has(key);
  }
  mark(key) {
    this.gc();
    this.seen.set(key, this.now() + this.ttlMs);
  }
  /**
   * Atomic check-and-mark: returns false if the key was already marked.
   * Single-threaded JS makes has+set in one synchronous block race-free —
   * which mark() called after a separate has() (across an await) is NOT.
   */
  checkAndMark(key) {
    this.gc();
    if (this.seen.has(key)) return false;
    this.seen.set(key, this.now() + this.ttlMs);
    return true;
  }
  gc() {
    const t = this.now();
    for (const [k, exp] of this.seen) if (exp <= t) this.seen.delete(k);
  }
}

/** Everything /verify learns about a payment, or a thrown VerifyReject. */
class VerifyReject extends Error {
  constructor(reason, detail) {
    super(detail || reason);
    this.reason = reason;
  }
}

function createFacilitator({
  network,
  signer, // {publicKey, sign} — the fee-payer sponsor keypair
  rpc, // {call(method, params)} — injectable; tests pass a mock
  replay = new ReplayStore(),
  confirmAttempts = 20,
  sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
  logger = console,
} = {}) {
  if (!network) throw new Error('facilitator needs a CAIP-2 network');
  if (!signer) throw new Error('facilitator needs a fee-payer signer');
  if (!rpc) throw new Error('facilitator needs an rpc client');

  function checkRequest(body) {
    if (!body || body.x402Version !== 2) throw new VerifyReject('invalid_payload', 'x402Version must be 2');
    const req = body.paymentRequirements;
    const pay = body.paymentPayload;
    if (!req || !pay) throw new VerifyReject('invalid_payload', 'paymentPayload and paymentRequirements required');
    if (req.scheme !== SCHEME) throw new VerifyReject('invalid_scheme', `only ${SCHEME} is supported`);
    if (req.network !== network) throw new VerifyReject('invalid_network', `this facilitator serves ${network}`);
    if (!/^\d+$/.test(String(req.amount))) throw new VerifyReject('invalid_payload', 'amount must be atomic integer string');
    const feePayer = req.extra && req.extra.feePayer;
    if (feePayer !== signer.publicKey) {
      throw new VerifyReject('invalid_payload', 'extra.feePayer is not this facilitator\'s sponsor');
    }
    if (!pay.payload || typeof pay.payload.transaction !== 'string') {
      throw new VerifyReject('invalid_exact_svm_payload_transaction', 'payload.transaction (base64) required');
    }
    return { req, pay };
  }

  /** Static + RPC verification. Returns facts /settle also needs. */
  async function inspect(body) {
    const { req, pay } = checkRequest(body);

    let tx;
    try {
      tx = sol.parseTransaction(pay.payload.transaction);
    } catch (e) {
      throw new VerifyReject('invalid_exact_svm_payload_transaction', `unparseable transaction: ${e.message}`);
    }
    if (tx.addressTableLookupCount > 0) {
      throw new VerifyReject('invalid_exact_svm_payload_transaction', 'address table lookups are not allowed');
    }
    if (tx.signatures.length !== tx.header.numRequiredSignatures) {
      throw new VerifyReject('invalid_exact_svm_payload_transaction', 'signature count does not match header');
    }

    // Fee payer isolation: account 0 pays fees and MUST be our sponsor —
    // we never co-sign a transaction that spends anyone else's SOL, and we
    // never let our sponsor appear anywhere but the fee slot.
    if (tx.accountKeys[0] !== signer.publicKey) {
      throw new VerifyReject('invalid_exact_svm_payload_fee_payer', 'fee payer is not the advertised sponsor');
    }

    // Instruction whitelist per the spec's static-layout check: 1..7 of
    // SetComputeUnitLimit / SetComputeUnitPrice / Memo / Lighthouse guards
    // (wallet-injected, spec says MUST be allowed) / exactly one spl-token or
    // token-2022 TransferChecked. Anything else is rejected — an unknown
    // program in a tx we fee-pay is an open cheque.
    if (tx.instructions.length < 1 || tx.instructions.length > 7) {
      throw new VerifyReject('invalid_exact_svm_payload_instructions', `instruction count ${tx.instructions.length}`);
    }
    let transfer = null;
    let tokenProgram = null;
    let memoData = null;
    for (const ix of tx.instructions) {
      const program = tx.accountKeys[ix.programIdIndex];
      // Spec §2.1.1 fee-payer isolation: the sponsor must appear NOWHERE but
      // the fee slot — not as a program, not in any instruction's accounts
      // (closes the multisig-signer and memo-signer edges; the runtime can
      // then never authorize the sponsor for anything but the network fee).
      if (program === signer.publicKey ||
          ix.accounts.some((ai) => tx.accountKeys[ai] === signer.publicKey)) {
        throw new VerifyReject('invalid_exact_svm_payload_fee_payer', 'sponsor may only occupy the fee-payer slot');
      }
      if (program === sol.TOKEN_PROGRAM_ID || program === sol.TOKEN_2022_PROGRAM_ID) {
        if (ix.data[0] !== 12) throw new VerifyReject('invalid_exact_svm_payload_instructions', 'token instruction is not TransferChecked');
        if (transfer) throw new VerifyReject('invalid_exact_svm_payload_instructions', 'multiple transfers');
        if (ix.data.length < 10 || ix.accounts.length < 4) {
          throw new VerifyReject('invalid_exact_svm_payload_instructions', 'malformed TransferChecked');
        }
        transfer = ix;
        tokenProgram = program;
      } else if (program === sol.COMPUTE_BUDGET_PROGRAM_ID) {
        const tag = ix.data[0];
        if (tag !== 2 && tag !== 3) throw new VerifyReject('invalid_exact_svm_payload_instructions', 'disallowed compute-budget instruction');
        if (tag === 3) {
          const price = Buffer.from(ix.data).readBigUInt64LE(1);
          if (price > MAX_COMPUTE_UNIT_PRICE_MICRO_LAMPORTS) {
            throw new VerifyReject('invalid_exact_svm_payload_instructions', 'compute unit price above cap');
          }
        }
      } else if (program === sol.MEMO_PROGRAM_ID) {
        memoData = Buffer.from(ix.data).toString('utf8');
      } else if (program === sol.LIGHTHOUSE_PROGRAM_ID) {
        // Read-only assertion guards injected by Phantom/Solflare; inert for
        // us (they can only make the tx fail, never move funds).
      } else {
        throw new VerifyReject('invalid_exact_svm_payload_instructions', `disallowed program ${program}`);
      }
    }
    if (!transfer) throw new VerifyReject('invalid_exact_svm_payload_instructions', 'no TransferChecked found');
    if (req.extra && req.extra.memo !== undefined && memoData !== req.extra.memo) {
      throw new VerifyReject('invalid_exact_svm_payload_instructions', 'required memo missing or wrong');
    }

    // TransferChecked accounts: [source, mint, destination, authority, ...]
    const source = tx.accountKeys[transfer.accounts[0]];
    const mint = tx.accountKeys[transfer.accounts[1]];
    const destination = tx.accountKeys[transfer.accounts[2]];
    const authority = tx.accountKeys[transfer.accounts[3]];
    const amount = Buffer.from(transfer.data).readBigUInt64LE(1);
    const decimals = transfer.data[9];

    if (mint !== req.asset) throw new VerifyReject('invalid_exact_svm_payload_mint', `transfer mint ${mint} != required asset`);
    if (amount !== BigInt(req.amount)) {
      throw new VerifyReject('invalid_exact_svm_payload_amount', `transfer amount ${amount} != required ${req.amount}`);
    }
    if (authority === signer.publicKey) {
      throw new VerifyReject('invalid_exact_svm_payload_fee_payer', 'sponsor cannot be the transfer authority');
    }

    // The authority must actually have signed this exact message.
    const authorityIdx = tx.accountKeys.indexOf(authority);
    if (authorityIdx < 0 || authorityIdx >= tx.header.numRequiredSignatures) {
      throw new VerifyReject('invalid_exact_svm_payload_transaction', 'authority is not a signer');
    }
    const authoritySig = tx.signatures[authorityIdx];
    if (sol.isPlaceholderSignature(authoritySig) ||
        !sol.verifySignature(tx.messageBytes, authoritySig, authority)) {
      throw new VerifyReject('invalid_exact_svm_payload_signature', 'authority signature missing or invalid');
    }

    // Destination: token account owned by payTo, holding the required mint.
    let destInfo;
    try {
      destInfo = await rpc.call('getAccountInfo', [destination, { encoding: 'base64' }]);
    } catch (e) {
      throw new VerifyReject('unexpected_verify_error', `rpc getAccountInfo failed: ${e.message}`);
    }
    if (!destInfo || !destInfo.value) throw new VerifyReject('invalid_exact_svm_payload_recipient', 'destination account does not exist');
    if (destInfo.value.owner !== tokenProgram) {
      throw new VerifyReject('invalid_exact_svm_payload_recipient', 'destination is not owned by the token program');
    }
    let destAcc;
    try {
      destAcc = sol.parseTokenAccount(destInfo.value.data[0]);
    } catch (e) {
      throw new VerifyReject('invalid_exact_svm_payload_recipient', `destination unparsable: ${e.message}`);
    }
    if (destAcc.mint !== req.asset) throw new VerifyReject('invalid_exact_svm_payload_recipient', 'destination holds a different mint');
    if (destAcc.owner !== req.payTo) throw new VerifyReject('invalid_exact_svm_payload_recipient', 'destination is not owned by payTo');

    // Source: same mint, and funded.
    let srcInfo;
    try {
      srcInfo = await rpc.call('getAccountInfo', [source, { encoding: 'base64' }]);
    } catch (e) {
      throw new VerifyReject('unexpected_verify_error', `rpc getAccountInfo failed: ${e.message}`);
    }
    if (!srcInfo || !srcInfo.value) throw new VerifyReject('invalid_exact_svm_payload_source', 'source account does not exist');
    let srcAcc;
    try {
      srcAcc = sol.parseTokenAccount(srcInfo.value.data[0]);
    } catch (e) {
      throw new VerifyReject('invalid_exact_svm_payload_source', `source unparsable: ${e.message}`);
    }
    if (srcAcc.mint !== req.asset) throw new VerifyReject('invalid_exact_svm_payload_source', 'source holds a different mint');
    if (srcAcc.amount < amount) throw new VerifyReject('insufficient_funds', `source has ${srcAcc.amount}, needs ${amount}`);

    const replayKey = require('node:crypto').createHash('sha256').update(tx.messageBytes).digest('hex');
    if (replay.has(replayKey)) throw new VerifyReject('invalid_transaction_state', 'transaction already settled or settling');

    return { tx, req, payer: authority, amount, decimals, replayKey };
  }

  return {
    signer,
    network,
    replay,

    /** Spec: read-only, MUST NOT commit state. */
    async verify(body) {
      try {
        const { payer } = await inspect(body);
        return { isValid: true, payer };
      } catch (e) {
        if (e instanceof VerifyReject) {
          return { isValid: false, invalidReason: e.reason, invalidDetail: e.message };
        }
        logger.error('verify blew up', e);
        return { isValid: false, invalidReason: 'unexpected_verify_error' };
      }
    },

    async settle(body) {
      let facts;
      try {
        facts = await inspect(body);
      } catch (e) {
        if (e instanceof VerifyReject) {
          return { success: false, errorReason: e.reason, transaction: '', network };
        }
        logger.error('settle inspect blew up', e);
        return { success: false, errorReason: 'unexpected_settle_error', transaction: '', network };
      }

      const { tx, payer, amount, replayKey } = facts;
      // Mark BEFORE broadcast — see file header for why the mark stays on
      // failure. Atomic check-and-mark, not mark(): inspect()'s replay check
      // ran before awaits resolved, so two CONCURRENT settles of the same tx
      // could both pass it (the spec's "duplicate settlement" race). Whoever
      // reaches this line first wins; the loser never broadcasts.
      if (!replay.checkAndMark(replayKey)) {
        return { success: false, errorReason: 'invalid_transaction_state', transaction: '', network, payer };
      }

      const feeSig = signer.sign(tx.messageBytes);
      const signatures = tx.signatures.slice();
      signatures[0] = feeSig; // fee payer is account 0, checked in inspect()
      const wire = sol.serializeTransaction(signatures, tx.messageBytes);
      const txSig = sol.base58Encode(feeSig);

      try {
        await rpc.call('sendTransaction', [wire, { encoding: 'base64', skipPreflight: false, maxRetries: 3 }]);
      } catch (e) {
        logger.error('sendTransaction failed', e.message);
        return { success: false, errorReason: 'unexpected_settle_error', transaction: '', network, payer };
      }

      for (let i = 0; i < confirmAttempts; i++) {
        let statuses;
        try {
          statuses = await rpc.call('getSignatureStatuses', [[txSig]]);
        } catch (e) {
          statuses = null;
        }
        const st = statuses && statuses.value && statuses.value[0];
        if (st && st.err) {
          return { success: false, errorReason: 'invalid_transaction_state', transaction: txSig, network, payer };
        }
        if (st && (st.confirmationStatus === 'confirmed' || st.confirmationStatus === 'finalized')) {
          return { success: true, transaction: txSig, network, payer, amount: amount.toString() };
        }
        await sleep(500);
      }
      // Broadcast but unconfirmed within our patience: report failure, keep
      // the replay mark (the tx may still land; do not let it be re-settled).
      return { success: false, errorReason: 'unexpected_settle_error', transaction: txSig, network, payer };
    },

    supported() {
      return {
        kinds: [{ x402Version: 2, scheme: SCHEME, network, extra: { feePayer: signer.publicKey } }],
        extensions: [],
        signers: { [network]: [signer.publicKey] },
      };
    },
  };
}

/* ---------------------------------------------------------- HTTP server -- */

const MAX_BODY = 64 * 1024; // a solana tx is <=1232 bytes; 64K is generous

function readJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY) { reject(new Error('body too large')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')); }
      catch (e) { reject(new Error('invalid json')); }
    });
    req.on('error', reject);
  });
}

function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) });
  res.end(body);
}

function createFacilitatorServer(facilitator, { logger = console } = {}) {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    try {
      if (req.method === 'GET' && url.pathname === '/supported') {
        return sendJson(res, 200, facilitator.supported());
      }
      if (req.method === 'GET' && url.pathname === '/healthz') {
        return sendJson(res, 200, { ok: true, role: 'x402-facilitator', network: facilitator.network });
      }
      if (req.method === 'POST' && url.pathname === '/verify') {
        return sendJson(res, 200, await facilitator.verify(await readJson(req)));
      }
      if (req.method === 'POST' && url.pathname === '/settle') {
        return sendJson(res, 200, await facilitator.settle(await readJson(req)));
      }
      return sendJson(res, 404, { error: 'not_found' });
    } catch (e) {
      logger.error('facilitator request failed', e.message);
      return sendJson(res, 400, { error: 'bad_request' });
    }
  });
}

/* -------------------------------------------------------------- standalone */

function buildFromEnv() {
  const cfg = cfgMod.load();
  let signer;
  if (cfg.wanmFeePayerSecret) {
    signer = sol.keypairFromSeed(sol.seedFromString(cfg.wanmFeePayerSecret));
  } else {
    signer = sol.randomKeypair();
    console.warn(
      '[x402-facilitator] WANM_FEEPAYER_SECRET not set — generated EPHEMERAL sponsor ' +
      `${signer.publicKey}. Fine for the demo; useless for production (changes every boot).`
    );
  }
  const rpc = sol.rpcClient(cfg.solanaRpcUrl);
  return { cfg, facilitator: createFacilitator({ network: cfg.networkSvm, signer, rpc }) };
}

if (require.main === module) {
  if (process.env.ANM_X402_ENABLED !== '1') {
    console.error('refusing to start: set ANM_X402_ENABLED=1 (this scaffold is off by default)');
    process.exit(1);
  }
  const { cfg, facilitator } = buildFromEnv();
  const server = createFacilitatorServer(facilitator);
  server.listen(cfg.facilitatorPort, '127.0.0.1', () => {
    console.log(`x402 wANM facilitator on 127.0.0.1:${cfg.facilitatorPort} network=${cfg.networkSvm} feePayer=${facilitator.signer.publicKey}`);
  });
}

module.exports = { createFacilitator, createFacilitatorServer, ReplayStore, VerifyReject, buildFromEnv };
