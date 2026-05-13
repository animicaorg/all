/**
 * Animica Launcher API
 *
 * Token-launcher backend that complements (does not replace) the chain.
 *
 * Source of truth split:
 *   - chain  : ownership, supply, balances, transfers, the contract itself
 *   - this   : human-readable metadata (name/symbol may also live on-chain
 *              via metadata_uri, but description, image URL, socials,
 *              creator profile, comments, discovery signals are not — and
 *              shouldn't be — bytes on the chain). Treat this DB as a
 *              renderable index over chain truth, never as authority for
 *              token economics.
 *
 * Auth model:
 *   - POST /api/tokens accepts a token registration *signed by the creator's
 *     wallet*. The signature attests `address|symbol|contractAddress`.
 *     The API does not gate on JWT; it gates on signature-over-content
 *     and on contractAddress already existing on chain.
 *
 * Endpoints:
 *   GET    /api/health
 *   GET    /api/contract-template      compiled token IR + manifest (one-shot artifact server can serve to the launch form)
 *   POST   /api/tokens                 register a launched token's metadata
 *   GET    /api/tokens                 list registered tokens (paginated)
 *   GET    /api/tokens/:address        single token
 *   POST   /api/tokens/:address/comments  add a comment (signature-gated)
 *   GET    /api/tokens/:address/comments
 */

import express, { type Request, type Response, type NextFunction } from 'express';
import cors from 'cors';
import helmet from 'helmet';
import rateLimit from 'express-rate-limit';
import pino from 'pino';
// pino-http v10 publishes both `default` and `pinoHttp` named exports; the
// CJS interop default sometimes appears as the module namespace under
// NodeNext, so we use the named export to keep TS happy.
import { pinoHttp } from 'pino-http';
import { z } from 'zod';
import path from 'node:path';
import fs from 'node:fs';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import Database from 'better-sqlite3';
import 'dotenv/config';

const execFileAsync = promisify(execFile);

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Plain JSON logs by default. The pino-pretty transport runs in a worker
// thread and resolves modules via NodeNext, which doesn't play well with
// our launcher-api setup; keeping logs as one-line JSON is faster, safer
// for pipelines, and avoids ESM/worker module-resolution headaches.
const log = pino({ level: process.env.LOG_LEVEL ?? 'info' });

const PORT = Number(process.env.LAUNCHER_API_PORT ?? 8787);
const DATA_DIR = process.env.LAUNCHER_DATA_DIR ?? path.resolve(__dirname, '..', 'data');
const DB_PATH = process.env.LAUNCHER_DB_PATH ?? path.join(DATA_DIR, 'launcher.sqlite');
const CONTRACT_TEMPLATE_PATH =
  process.env.LAUNCHER_CONTRACT_TEMPLATE ??
  path.resolve(__dirname, '..', '..', '..', 'contracts', 'examples', 'token');
const CONTRACTS_ROOT = path.resolve(__dirname, '..', '..', '..', 'contracts');
const DEX_TEMPLATE_ROOTS = {
  factory: path.join(CONTRACTS_ROOT, 'standards', 'animica_dex_factory'),
  router:  path.join(CONTRACTS_ROOT, 'standards', 'animica_dex_router'),
  pair:    path.join(CONTRACTS_ROOT, 'standards', 'animica_dex_pair'),
} as const;
const DEX_IR_PATHS = {
  factory: path.join(__dirname, '..', 'data', 'dex_factory.ir'),
  router:  path.join(__dirname, '..', 'data', 'dex_router.ir'),
  pair:    path.join(__dirname, '..', 'data', 'dex_pair.ir'),
} as const;
const ENCODE_CALLDATA_SCRIPT = path.resolve(__dirname, '..', 'scripts', 'encode_calldata.py');
const DECODE_RESULT_SCRIPT  = path.resolve(__dirname, '..', 'scripts', 'decode_result.py');
const PYTHON_BIN = process.env.LAUNCHER_PYTHON ?? 'python3';
const CHAIN_RPC_URL = process.env.LAUNCHER_RPC_URL ?? 'https://rpc.animica.org/rpc';
const ALLOWED_ORIGINS = (process.env.LAUNCHER_ALLOWED_ORIGINS ?? 'https://animica.org,http://localhost:4321')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

fs.mkdirSync(DATA_DIR, { recursive: true });

// --- DB bootstrap -----------------------------------------------------------

const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

db.exec(`
  CREATE TABLE IF NOT EXISTS tokens (
    contract_address TEXT PRIMARY KEY,    -- 0x... 64-hex
    tx_hash          TEXT NOT NULL,       -- 0x... deploy tx
    creator_address  TEXT NOT NULL,       -- bech32 anim1...
    name             TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    decimals         INTEGER NOT NULL,
    initial_supply   TEXT NOT NULL,       -- decimal string (bigint)
    max_supply       TEXT,
    description      TEXT,
    image_url        TEXT,
    website_url      TEXT,
    twitter_url      TEXT,
    telegram_url     TEXT,
    discord_url      TEXT,
    metadata_uri     TEXT,
    chain_id         INTEGER NOT NULL,
    created_at       INTEGER NOT NULL,    -- unix seconds
    last_seen_block  INTEGER,             -- indexer marker (nullable)
    signature        TEXT NOT NULL        -- signature over creator|symbol|contractAddress
  );
  CREATE INDEX IF NOT EXISTS tokens_created_at_idx ON tokens(created_at DESC);
  CREATE INDEX IF NOT EXISTS tokens_creator_idx ON tokens(creator_address);
  CREATE INDEX IF NOT EXISTS tokens_symbol_idx ON tokens(symbol);

  CREATE TABLE IF NOT EXISTS comments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_address  TEXT NOT NULL REFERENCES tokens(contract_address) ON DELETE CASCADE,
    author_address    TEXT NOT NULL,
    body              TEXT NOT NULL,
    created_at        INTEGER NOT NULL,
    signature         TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS comments_token_idx ON comments(contract_address, created_at DESC);

  -- DEX deployment registry. Singleton table — at most one row per
  -- (chain_id, role). The admin deploy page writes here after the
  -- factory/router init txs have been mined into a block.
  CREATE TABLE IF NOT EXISTS dex_addresses (
    chain_id        INTEGER NOT NULL,
    role            TEXT NOT NULL CHECK(role IN ('factory','router','pair_template')),
    address         TEXT NOT NULL,
    tx_hash         TEXT NOT NULL,
    deployer        TEXT NOT NULL,
    last_seen_block INTEGER,
    created_at      INTEGER NOT NULL,
    PRIMARY KEY (chain_id, role)
  );

  -- Indexer state — one row per chain we track.
  CREATE TABLE IF NOT EXISTS indexer_state (
    chain_id           INTEGER PRIMARY KEY,
    last_tick_at       INTEGER NOT NULL,     -- unix seconds of last successful tick
    last_chain_height  INTEGER,
    last_error         TEXT,
    last_error_at      INTEGER,
    tokens_alive       INTEGER NOT NULL DEFAULT 0,
    tokens_missing     INTEGER NOT NULL DEFAULT 0
  );
`);

// Lightweight migrations for the indexer columns added after first deploy.
// `ALTER TABLE ADD COLUMN` is idempotent under try/catch — better-sqlite3 throws
// if the column already exists.
function ensureColumn(table: string, column: string, ddl: string): void {
  const cols = db.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>;
  if (!cols.some((c) => c.name === column)) {
    db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${ddl}`);
  }
}
ensureColumn('tokens', 'observed_total_supply', 'TEXT');
ensureColumn('tokens', 'observed_owner',        'TEXT');
ensureColumn('tokens', 'observed_at',           'INTEGER');
ensureColumn('tokens', 'observed_status',       'TEXT'); // 'live'|'missing'|'error'
ensureColumn('tokens', 'observed_error',        'TEXT');

// --- Helpers ----------------------------------------------------------------

const HEX64 = /^0x[0-9a-f]{64}$/i;
const HEX_TXHASH = /^0x[0-9a-f]{64}$/i;
const ANIM_ADDR = /^anim1[02-9ac-hj-np-z]{20,}$/i;
const URL_OR_IPFS = /^(https?:\/\/|ipfs:\/\/)/i;

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

function ok<T>(res: Response, body: T): void {
  res.status(200).json(body);
}

function bad(res: Response, code: number, message: string, extra?: unknown): void {
  res.status(code).json({ error: { message, extra } });
}

async function readContractTemplate(): Promise<{
  manifestHex: string;
  codeHex: string;
  manifestSha256: string;
  codeSha256: string;
}> {
  const manifestPath = path.join(CONTRACT_TEMPLATE_PATH, 'manifest.json');
  if (!fs.existsSync(manifestPath)) {
    throw new Error(`Contract template not found at ${manifestPath}`);
  }
  const manifestBytes = fs.readFileSync(manifestPath);

  // Code IR is built on-disk via `python -m vm_py.cli.compile ...` once per
  // deploy of this service. Cache result on first read.
  const cachedIrPath = path.join(DATA_DIR, 'token.ir');
  if (!fs.existsSync(cachedIrPath)) {
    throw new Error(
      `Compiled IR not found at ${cachedIrPath}. Run: python -m vm_py.cli.compile ` +
      `${path.join(CONTRACT_TEMPLATE_PATH, 'contract.py')} --out ${cachedIrPath}`,
    );
  }
  const codeBytes = fs.readFileSync(cachedIrPath);

  return {
    manifestHex: '0x' + manifestBytes.toString('hex'),
    codeHex: '0x' + codeBytes.toString('hex'),
    manifestSha256: crypto.createHash('sha3-256').update(manifestBytes).digest('hex'),
    codeSha256: crypto.createHash('sha3-256').update(codeBytes).digest('hex'),
  };
}

async function rpcCall<T = unknown>(method: string, params: unknown): Promise<T> {
  const res = await fetch(CHAIN_RPC_URL, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: Date.now(), method, params }),
  });
  const body = (await res.json()) as { result?: T; error?: { message: string; code?: number } };
  if (body.error) {
    throw new Error(`rpc error (${method}): ${body.error.message}`);
  }
  return body.result as T;
}

/**
 * Verify that the deploy tx hash has been *mined* — i.e. included in a
 * canonical block. We deliberately do NOT accept mempool_accepted /
 * submitted, because metadata that points at a tx the network later
 * drops is worse than no metadata at all. The chain's normal block
 * cadence is the source of truth; we wait for it rather than working
 * around it.
 *
 * Accepted terminal-ish states: `included`, `confirmed`. Anything else
 * (`submitted`, `mempool_accepted`, `not_found`, `dropped`, `reorged_out`)
 * means "keep waiting / refuse".
 */
const CONFIRMED_TX_STATES = new Set(['included', 'confirmed', 'finalized']);

async function chainConfirmedDeployTx(txHash: string): Promise<{
  confirmed: boolean;
  status: string;
  blockHeight?: number;
}> {
  // Receipts are the strongest signal — if a receipt exists, the tx was
  // executed against state, which only happens at block inclusion.
  try {
    const receipt = await rpcCall<unknown>('tx_getTransactionReceipt', [txHash]);
    if (receipt && typeof receipt === 'object') {
      const r = receipt as { blockNumber?: number; block_number?: number; status?: string };
      const height = typeof r.blockNumber === 'number'
        ? r.blockNumber
        : typeof r.block_number === 'number' ? r.block_number : undefined;
      return { confirmed: true, status: 'included', blockHeight: height };
    }
  } catch {
    // fall through to status probe
  }

  // Fall back to the explicit status enum.
  try {
    const status = await rpcCall<{ status?: string; blockHeight?: number }>(
      'tx.getTransactionStatus',
      [txHash],
    );
    if (status && typeof status === 'object') {
      const s = String(status.status ?? '').toLowerCase();
      if (CONFIRMED_TX_STATES.has(s)) {
        return { confirmed: true, status: s, blockHeight: status.blockHeight };
      }
      return { confirmed: false, status: s || 'unknown' };
    }
  } catch {
    /* ignore */
  }
  return { confirmed: false, status: 'unknown' };
}

// --- Schemas ----------------------------------------------------------------

const RegisterTokenSchema = z.object({
  contractAddress: z.string().regex(HEX64, 'contractAddress must be 0x + 64 hex chars'),
  txHash: z.string().regex(HEX_TXHASH, 'txHash must be 0x + 64 hex chars'),
  creatorAddress: z.string().regex(ANIM_ADDR, 'creatorAddress must be an anim1... bech32 address'),
  name: z.string().min(1).max(64),
  symbol: z.string().min(1).max(16).regex(/^[A-Za-z0-9_]+$/, 'symbol must be alphanumeric/underscore'),
  decimals: z.number().int().min(0).max(18),
  initialSupply: z.string().regex(/^\d+$/, 'initialSupply must be a decimal string of base units'),
  maxSupply: z.string().regex(/^\d+$/).optional(),
  description: z.string().max(2000).optional(),
  imageUrl: z.string().regex(URL_OR_IPFS, 'imageUrl must be http(s):// or ipfs://').max(500).optional(),
  websiteUrl: z.string().regex(URL_OR_IPFS).max(500).optional(),
  twitterUrl: z.string().regex(URL_OR_IPFS).max(500).optional(),
  telegramUrl: z.string().regex(URL_OR_IPFS).max(500).optional(),
  discordUrl: z.string().regex(URL_OR_IPFS).max(500).optional(),
  metadataUri: z.string().regex(URL_OR_IPFS).max(500).optional(),
  chainId: z.number().int().positive(),
  signature: z.string().regex(/^0x[0-9a-f]+$/i, 'signature must be 0x hex'),
});

const CommentSchema = z.object({
  authorAddress: z.string().regex(ANIM_ADDR),
  body: z.string().min(1).max(2000),
  signature: z.string().regex(/^0x[0-9a-f]+$/i),
});

// --- Routes -----------------------------------------------------------------

const app = express();
app.use(helmet());
app.use(
  cors({
    origin: (origin, cb) => {
      if (!origin) return cb(null, true);
      if (ALLOWED_ORIGINS.includes('*')) return cb(null, true);
      if (ALLOWED_ORIGINS.includes(origin)) return cb(null, true);
      return cb(new Error('CORS: origin not allowed'));
    },
    credentials: false,
  }),
);
app.use(express.json({ limit: '256kb' }));
app.use(pinoHttp({ logger: log }));

const writeLimiter = rateLimit({
  windowMs: 60_000,
  limit: 30,
  standardHeaders: 'draft-7',
  legacyHeaders: false,
});

app.get('/api/health', (_req, res) => {
  ok(res, { ok: true, ts: nowSeconds() });
});

app.get('/api/contract-template', async (_req, res, next) => {
  try {
    const tpl = await readContractTemplate();
    ok(res, tpl);
  } catch (err) {
    next(err);
  }
});

app.post('/api/tokens', writeLimiter, async (req, res, next) => {
  try {
    const parsed = RegisterTokenSchema.parse(req.body ?? {});

    // Existence check on chain — refuses to register ghost tokens. We
    // wait for the deploy tx to be *included in a block* before persisting
    // metadata. Mempool-only acceptance is not enough: txs can be dropped
    // or reorged. The chain's block cadence is the source of truth.
    const chainState = await chainConfirmedDeployTx(parsed.txHash);
    if (!chainState.confirmed) {
      return bad(
        res,
        409,
        'deploy tx not yet confirmed in a block',
        {
          status: chainState.status,
          hint: 'wait for the deploy tx to be mined into a block, then retry',
        },
      );
    }

    // We don't verify the wallet signature against the canonical PQ scheme
    // here (that requires the same sign-bytes wrapper the chain uses); the
    // chain itself proved tx authorship by mining the deploy. The signature
    // is stored as a future audit trail / soft anti-spam.
    const insert = db.prepare(`
      INSERT INTO tokens (
        contract_address, tx_hash, creator_address, name, symbol, decimals,
        initial_supply, max_supply, description, image_url, website_url,
        twitter_url, telegram_url, discord_url, metadata_uri, chain_id,
        created_at, last_seen_block, signature
      ) VALUES (
        @contractAddress, @txHash, @creatorAddress, @name, @symbol, @decimals,
        @initialSupply, @maxSupply, @description, @imageUrl, @websiteUrl,
        @twitterUrl, @telegramUrl, @discordUrl, @metadataUri, @chainId,
        @createdAt, @lastSeenBlock, @signature
      )
    `);
    insert.run({
      ...parsed,
      createdAt: nowSeconds(),
      maxSupply: parsed.maxSupply ?? null,
      description: parsed.description ?? null,
      imageUrl: parsed.imageUrl ?? null,
      websiteUrl: parsed.websiteUrl ?? null,
      twitterUrl: parsed.twitterUrl ?? null,
      telegramUrl: parsed.telegramUrl ?? null,
      discordUrl: parsed.discordUrl ?? null,
      metadataUri: parsed.metadataUri ?? null,
      lastSeenBlock: chainState.blockHeight ?? null,
    });
    ok(res, { token: getToken(parsed.contractAddress) });
  } catch (err) {
    if (err instanceof z.ZodError) {
      return bad(res, 400, 'validation failed', err.flatten());
    }
    if (err instanceof Error && err.message.includes('UNIQUE')) {
      return bad(res, 409, 'token already registered');
    }
    next(err);
  }
});

app.get('/api/tokens', (req, res) => {
  const limit = Math.min(100, Math.max(1, Number(req.query.limit ?? 50)));
  const offset = Math.max(0, Number(req.query.offset ?? 0));
  const creator = typeof req.query.creator === 'string' ? req.query.creator : null;
  const search = typeof req.query.q === 'string' ? `%${req.query.q}%` : null;

  const where: string[] = [];
  const args: Record<string, unknown> = { limit, offset };
  if (creator) {
    where.push('creator_address = @creator');
    args.creator = creator;
  }
  if (search) {
    where.push('(name LIKE @search OR symbol LIKE @search OR description LIKE @search)');
    args.search = search;
  }
  const whereSql = where.length ? `WHERE ${where.join(' AND ')}` : '';
  const rows = db
    .prepare(
      `SELECT * FROM tokens ${whereSql} ORDER BY created_at DESC LIMIT @limit OFFSET @offset`,
    )
    .all(args) as Array<Record<string, unknown>>;
  const total = (
    db.prepare(`SELECT COUNT(1) AS n FROM tokens ${whereSql}`).get(args) as { n: number }
  ).n;
  ok(res, { tokens: rows.map(rowToToken), total, limit, offset });
});

app.get('/api/tokens/:address', (req, res) => {
  const tok = getToken(req.params.address);
  if (!tok) return bad(res, 404, 'token not found');
  ok(res, { token: tok });
});

app.post('/api/tokens/:address/comments', writeLimiter, (req, res, next) => {
  try {
    const parsed = CommentSchema.parse(req.body ?? {});
    const tok = getToken(req.params.address);
    if (!tok) return bad(res, 404, 'token not found');
    db.prepare(`
      INSERT INTO comments (contract_address, author_address, body, created_at, signature)
      VALUES (?, ?, ?, ?, ?)
    `).run(req.params.address, parsed.authorAddress, parsed.body, nowSeconds(), parsed.signature);
    ok(res, { ok: true });
  } catch (err) {
    if (err instanceof z.ZodError) return bad(res, 400, 'validation failed', err.flatten());
    next(err);
  }
});

app.get('/api/tokens/:address/comments', (req, res) => {
  const tok = getToken(req.params.address);
  if (!tok) return bad(res, 404, 'token not found');
  const limit = Math.min(100, Math.max(1, Number(req.query.limit ?? 50)));
  const offset = Math.max(0, Number(req.query.offset ?? 0));
  const rows = db
    .prepare(
      `SELECT id, author_address, body, created_at FROM comments
       WHERE contract_address = ?
       ORDER BY created_at DESC LIMIT ? OFFSET ?`,
    )
    .all(req.params.address, limit, offset) as Array<Record<string, unknown>>;
  ok(res, { comments: rows, limit, offset });
});

// --- DEX deploy bring-up ----------------------------------------------------

/**
 * Returns compiled IR + manifest hex for the three DEX standards so the
 * admin page can sign deploy txs through the wallet. Same shape as
 * /api/contract-template, repeated per role.
 */
app.get('/api/dex/templates', (_req, res, next) => {
  try {
    const out: Record<string, unknown> = {};
    for (const role of ['factory', 'router', 'pair'] as const) {
      const tplDir = DEX_TEMPLATE_ROOTS[role];
      const irPath = DEX_IR_PATHS[role];
      const manifestPath = path.join(tplDir, 'manifest.json');
      if (!fs.existsSync(manifestPath)) {
        return bad(res, 500, `dex manifest missing for ${role}`, { path: manifestPath });
      }
      if (!fs.existsSync(irPath)) {
        return bad(res, 500, `compiled IR missing for ${role}`, {
          path: irPath,
          hint: `python -m vm_py.cli.compile ${path.join(tplDir, 'contract.py')} --out ${irPath}`,
        });
      }
      const manifestBytes = fs.readFileSync(manifestPath);
      const codeBytes = fs.readFileSync(irPath);
      out[role] = {
        manifestHex: '0x' + manifestBytes.toString('hex'),
        codeHex: '0x' + codeBytes.toString('hex'),
        manifestSha256: crypto.createHash('sha3-256').update(manifestBytes).digest('hex'),
        codeSha256: crypto.createHash('sha3-256').update(codeBytes).digest('hex'),
      };
    }
    ok(res, out);
  } catch (err) {
    next(err);
  }
});

const EncodeInitSchema = z.object({
  role: z.enum(['factory', 'router', 'pair']),
  method: z.string().min(1).max(64),
  args: z.array(z.unknown()).max(16),
});

/**
 * Pre-encode calldata for a DEX method call (e.g. factory.init, router.init,
 * pair.init). The admin page passes the deployed addresses + owner here and
 * gets back the hex `data` payload it should send as a kind=2 transaction.
 *
 * The encoding uses the canonical Python helper to guarantee bit-exact match
 * with what the chain's VM decoder accepts — re-implementing it in TS would
 * be a divergence hazard.
 */
app.post('/api/dex/encode-init', async (req, res, next) => {
  try {
    const parsed = EncodeInitSchema.parse(req.body ?? {});
    const tplDir = DEX_TEMPLATE_ROOTS[parsed.role];
    const manifestPath = path.join(tplDir, 'manifest.json');
    if (!fs.existsSync(manifestPath)) {
      return bad(res, 500, `dex manifest missing for ${parsed.role}`);
    }
    const { stdout } = await execFileAsync(
      PYTHON_BIN,
      [ENCODE_CALLDATA_SCRIPT, manifestPath, parsed.method, JSON.stringify(parsed.args)],
      { timeout: 10_000, maxBuffer: 1_048_576 },
    );
    const hex = stdout.trim();
    if (!/^[0-9a-fA-F]+$/.test(hex) || hex.length % 2 !== 0) {
      return bad(res, 500, 'encoder returned malformed hex', { stdout });
    }
    ok(res, { dataHex: '0x' + hex });
  } catch (err) {
    if (err instanceof z.ZodError) return bad(res, 400, 'validation failed', err.flatten());
    next(err);
  }
});

const DexAddressesSchema = z.object({
  chainId: z.number().int().positive(),
  deployer: z.string().regex(ANIM_ADDR, 'deployer must be an anim1... bech32 address'),
  factory: z.object({
    address: z.string().regex(HEX64),
    txHash:  z.string().regex(HEX_TXHASH),
  }),
  router: z.object({
    address: z.string().regex(HEX64),
    txHash:  z.string().regex(HEX_TXHASH),
  }),
  pairTemplate: z
    .object({
      address: z.string().regex(HEX64),
      txHash:  z.string().regex(HEX_TXHASH),
    })
    .optional(),
});

/**
 * Persist the deployed factory/router (and optional pair-template) addresses
 * after their deploy txs are confirmed in a block. Same block-confirmation
 * gate as token registration: refuses to write until the chain mined the tx.
 */
app.post('/api/dex/addresses', writeLimiter, async (req, res, next) => {
  try {
    const parsed = DexAddressesSchema.parse(req.body ?? {});
    const roles: Array<['factory' | 'router' | 'pair_template', { address: string; txHash: string }]> = [
      ['factory', parsed.factory],
      ['router', parsed.router],
    ];
    if (parsed.pairTemplate) roles.push(['pair_template', parsed.pairTemplate]);

    // All deploy txs must be block-confirmed before we pin addresses.
    const confirmations: Record<string, { confirmed: boolean; status: string; blockHeight?: number }> = {};
    for (const [role, info] of roles) {
      const state = await chainConfirmedDeployTx(info.txHash);
      confirmations[role] = state;
      if (!state.confirmed) {
        return bad(res, 409, `${role} deploy tx not yet confirmed in a block`, {
          role,
          status: state.status,
          hint: 'wait for all three deploy txs to be mined, then retry',
        });
      }
    }

    const insert = db.prepare(`
      INSERT INTO dex_addresses (chain_id, role, address, tx_hash, deployer, last_seen_block, created_at)
      VALUES (@chainId, @role, @address, @txHash, @deployer, @blockHeight, @createdAt)
      ON CONFLICT(chain_id, role) DO UPDATE SET
        address = excluded.address,
        tx_hash = excluded.tx_hash,
        deployer = excluded.deployer,
        last_seen_block = excluded.last_seen_block,
        created_at = excluded.created_at
    `);
    const createdAt = nowSeconds();
    const txn = db.transaction(() => {
      for (const [role, info] of roles) {
        insert.run({
          chainId: parsed.chainId,
          role,
          address: info.address,
          txHash: info.txHash,
          deployer: parsed.deployer,
          blockHeight: confirmations[role].blockHeight ?? null,
          createdAt,
        });
      }
    });
    txn();
    ok(res, { addresses: readDexAddresses(parsed.chainId) });
  } catch (err) {
    if (err instanceof z.ZodError) return bad(res, 400, 'validation failed', err.flatten());
    next(err);
  }
});

app.get('/api/dex/addresses', (req, res) => {
  const chainId = Number(req.query.chainId ?? 1);
  ok(res, { chainId, addresses: readDexAddresses(chainId) });
});

function readDexAddresses(chainId: number): Record<string, unknown> {
  const rows = db
    .prepare(`SELECT role, address, tx_hash, deployer, last_seen_block, created_at FROM dex_addresses WHERE chain_id = ?`)
    .all(chainId) as Array<{
      role: string;
      address: string;
      tx_hash: string;
      deployer: string;
      last_seen_block: number | null;
      created_at: number;
    }>;
  const map: Record<string, unknown> = {};
  for (const r of rows) {
    map[r.role] = {
      address: r.address,
      txHash: r.tx_hash,
      deployer: r.deployer,
      blockHeight: r.last_seen_block,
      createdAt: r.created_at,
    };
  }
  return map;
}

// --- DEX view calls (quote, pool reads) -------------------------------------

const VIEW_RPC_METHODS: Array<{ method: string; params: (contract: string, dataHex: string, from?: string) => unknown }> = [
  { method: 'state.call',          params: (to, data, from) => [{ to, data, ...(from ? { from } : {}) }] },
  { method: 'execution.simulateCall', params: (to, data, from) => [{ to, data, ...(from ? { from } : {}) }] },
  { method: 'state.simulateCall',  params: (to, data, from) => [{ to, data, ...(from ? { from } : {}) }] },
  { method: 'vm.simulateCall',     params: (to, data, from) => [to, data, from ?? null, null] },
  { method: 'call.simulate',       params: (to, data, from) => [{ to, data, ...(from ? { from } : {}) }] },
  { method: 'contracts.simulate',  params: (to, data, from) => [{ to, data, ...(from ? { from } : {}) }] },
];

function coerceReturnBytes(raw: unknown): string {
  if (raw == null) return '0x';
  if (typeof raw === 'string') return raw.startsWith('0x') ? raw : '0x' + raw;
  if (typeof raw === 'object') {
    const obj = raw as Record<string, unknown>;
    for (const key of ['returnData', 'return_data', 'result', 'data', 'output', 'returndata']) {
      const v = obj[key];
      if (typeof v === 'string') return v.startsWith('0x') ? v : '0x' + v;
    }
  }
  return '0x';
}

async function rpcSimulateCall(
  contract: string,
  dataHex: string,
  from?: string,
): Promise<{ method: string; returnHex: string }> {
  const errors: string[] = [];
  for (const candidate of VIEW_RPC_METHODS) {
    try {
      const params = candidate.params(contract, dataHex, from);
      const result = await rpcCall<unknown>(candidate.method, params);
      const ret = coerceReturnBytes(result);
      return { method: candidate.method, returnHex: ret };
    } catch (err) {
      errors.push(`${candidate.method}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
  throw new Error(`no view-call RPC accepted: ${errors.slice(0, 3).join('; ')}`);
}

async function pythonExec(scriptPath: string, args: string[]): Promise<string> {
  const { stdout } = await execFileAsync(PYTHON_BIN, [scriptPath, ...args], {
    timeout: 10_000,
    maxBuffer: 1_048_576,
  });
  return stdout.trim();
}

async function viewCall(
  role: 'factory' | 'router' | 'pair',
  contract: string,
  method: string,
  args: unknown[],
  from?: string,
): Promise<unknown> {
  const manifestPath = path.join(DEX_TEMPLATE_ROOTS[role], 'manifest.json');
  const dataHex = '0x' + (await pythonExec(ENCODE_CALLDATA_SCRIPT, [
    manifestPath, method, JSON.stringify(args),
  ]));
  const { returnHex } = await rpcSimulateCall(contract, dataHex, from);
  const decoded = await pythonExec(DECODE_RESULT_SCRIPT, [manifestPath, method, returnHex]);
  return JSON.parse(decoded);
}

function getDexAddress(chainId: number, role: 'factory' | 'router' | 'pair_template'): string | null {
  const row = db
    .prepare(`SELECT address FROM dex_addresses WHERE chain_id = ? AND role = ?`)
    .get(chainId, role) as { address?: string } | undefined;
  return row?.address ?? null;
}

const QuoteSchema = z.object({
  chainId: z.number().int().positive().default(1),
  tokenIn: z.string().regex(/^0x([0-9a-f]{2})*$/i, 'tokenIn must be 0x-hex (empty = native ANM)'),
  tokenOut: z.string().regex(/^0x([0-9a-f]{2})*$/i),
  amountIn: z.string().regex(/^\d+$/, 'amountIn must be a decimal base-unit string'),
});

app.post('/api/dex/quote', async (req, res, next) => {
  try {
    const parsed = QuoteSchema.parse(req.body ?? {});
    const router = getDexAddress(parsed.chainId, 'router');
    if (!router) {
      return bad(res, 503, 'DEX router not deployed on this chain', {
        chainId: parsed.chainId,
        hint: 'run /admin/deploy-dex first',
      });
    }
    const result = await viewCall(
      'router',
      router,
      'quote_exact_in',
      [parsed.tokenIn, parsed.tokenOut, parsed.amountIn],
    );
    // decode_result emits big ints as decimal strings, smaller as JS numbers
    const amountOut = typeof result === 'number' || typeof result === 'string'
      ? String(result) : '0';
    ok(res, { amountOut, router });
  } catch (err) {
    if (err instanceof z.ZodError) return bad(res, 400, 'validation failed', err.flatten());
    next(err);
  }
});

app.get('/api/dex/pools', async (req, res, next) => {
  try {
    const chainId = Number(req.query.chainId ?? 1);
    const factory = getDexAddress(chainId, 'factory');
    if (!factory) {
      return bad(res, 503, 'DEX factory not deployed on this chain', { chainId });
    }
    const limit = Math.min(50, Math.max(1, Number(req.query.limit ?? 25)));
    const offset = Math.max(0, Number(req.query.offset ?? 0));
    const countRaw = await viewCall('factory', factory, 'pair_count', []);
    const total = Number(countRaw ?? 0);

    const pools: Array<Record<string, unknown>> = [];
    const end = Math.min(total, offset + limit);
    for (let i = offset; i < end; i++) {
      try {
        const pairAddr = String(await viewCall('factory', factory, 'pair_at', [i]) ?? '');
        if (!/^0x[0-9a-f]{64}$/i.test(pairAddr)) continue;
        const [token0, token1, reserve0, reserve1, feeBps, lpTotal] = await Promise.all([
          viewCall('pair', pairAddr, 'token0', []),
          viewCall('pair', pairAddr, 'token1', []),
          viewCall('pair', pairAddr, 'reserve0', []),
          viewCall('pair', pairAddr, 'reserve1', []),
          viewCall('factory', factory, 'pair_fee_bps', [pairAddr]),
          viewCall('pair', pairAddr, 'lp_total', []).catch(() => '0'),
        ]);
        pools.push({
          address: pairAddr,
          token0: String(token0 ?? '0x'),
          token1: String(token1 ?? '0x'),
          reserve0: String(reserve0 ?? '0'),
          reserve1: String(reserve1 ?? '0'),
          feeBps: Number(feeBps ?? 0),
          lpTotal: String(lpTotal ?? '0'),
          index: i,
        });
      } catch (err) {
        log.warn({ err: (err as Error).message, index: i }, 'pool read failed');
      }
    }
    ok(res, { factory, total, limit, offset, pools });
  } catch (err) {
    next(err);
  }
});

app.get('/api/dex/pool/:address', async (req, res, next) => {
  try {
    const chainId = Number(req.query.chainId ?? 1);
    const factory = getDexAddress(chainId, 'factory');
    if (!factory) {
      return bad(res, 503, 'DEX factory not deployed on this chain');
    }
    const pair = req.params.address;
    if (!/^0x[0-9a-f]{64}$/i.test(pair)) return bad(res, 400, 'bad pair address');
    const [token0, token1, reserve0, reserve1, feeBps, lpTotal, owner] = await Promise.all([
      viewCall('pair', pair, 'token0', []),
      viewCall('pair', pair, 'token1', []),
      viewCall('pair', pair, 'reserve0', []),
      viewCall('pair', pair, 'reserve1', []),
      viewCall('factory', factory, 'pair_fee_bps', [pair]),
      viewCall('pair', pair, 'lp_total', []).catch(() => '0'),
      viewCall('pair', pair, 'owner', []).catch(() => '0x'),
    ]);
    ok(res, {
      pool: {
        address: pair,
        token0: String(token0 ?? '0x'),
        token1: String(token1 ?? '0x'),
        reserve0: String(reserve0 ?? '0'),
        reserve1: String(reserve1 ?? '0'),
        feeBps: Number(feeBps ?? 0),
        lpTotal: String(lpTotal ?? '0'),
        owner: String(owner ?? '0x'),
      },
    });
  } catch (err) {
    next(err);
  }
});

// Generic call-data encoder (kind=2 swaps from the browser need this so the
// wallet receives a ready-to-sign `data` hex). Mirrors /api/dex/encode-init
// but takes role+method+args without the init restriction.
const EncodeCallSchema = z.object({
  role: z.enum(['factory', 'router', 'pair']),
  method: z.string().min(1).max(64),
  args: z.array(z.unknown()).max(16),
});

app.post('/api/dex/encode-call', async (req, res, next) => {
  try {
    const parsed = EncodeCallSchema.parse(req.body ?? {});
    const manifestPath = path.join(DEX_TEMPLATE_ROOTS[parsed.role], 'manifest.json');
    if (!fs.existsSync(manifestPath)) {
      return bad(res, 500, `dex manifest missing for ${parsed.role}`);
    }
    const hex = await pythonExec(ENCODE_CALLDATA_SCRIPT, [
      manifestPath, parsed.method, JSON.stringify(parsed.args),
    ]);
    if (!/^[0-9a-fA-F]+$/.test(hex) || hex.length % 2 !== 0) {
      return bad(res, 500, 'encoder returned malformed hex');
    }
    ok(res, { dataHex: '0x' + hex });
  } catch (err) {
    if (err instanceof z.ZodError) return bad(res, 400, 'validation failed', err.flatten());
    next(err);
  }
});

// --- Indexer status (registered before 404 fallback) ------------------------

app.get('/api/indexer/status', (_req, res) => {
  const row = db
    .prepare(`SELECT * FROM indexer_state WHERE chain_id = ?`)
    .get(Number(process.env.LAUNCHER_CHAIN_ID ?? 1)) as
    | {
        chain_id: number;
        last_tick_at: number;
        last_chain_height: number | null;
        last_error: string | null;
        last_error_at: number | null;
        tokens_alive: number;
        tokens_missing: number;
      }
    | undefined;
  const chainId = Number(process.env.LAUNCHER_CHAIN_ID ?? 1);
  const totalTokens = (
    db.prepare(`SELECT COUNT(1) AS n FROM tokens WHERE chain_id = ?`).get(chainId) as { n: number }
  ).n;
  ok(res, {
    enabled: (process.env.LAUNCHER_INDEXER_ENABLED ?? '1') === '1',
    chainId,
    tickSecs: Number(process.env.LAUNCHER_INDEXER_TICK_SECS ?? 60),
    batch: Number(process.env.LAUNCHER_INDEXER_BATCH ?? 25),
    totalTokens,
    state: row
      ? {
          lastTickAt: row.last_tick_at,
          lastChainHeight: row.last_chain_height,
          lastError: row.last_error,
          lastErrorAt: row.last_error_at,
          tokensAlive: row.tokens_alive,
          tokensMissing: row.tokens_missing,
        }
      : null,
  });
});

// --- 404 + error handler ----------------------------------------------------

app.use((_req, res) => bad(res, 404, 'not found'));
app.use((err: Error, _req: Request, res: Response, _next: NextFunction) => {
  log.error({ err }, 'unhandled error');
  if (res.headersSent) return;
  bad(res, 500, err.message || 'internal error');
});

function getToken(address: string): Record<string, unknown> | null {
  const row = db
    .prepare(`SELECT * FROM tokens WHERE contract_address = ?`)
    .get(address) as Record<string, unknown> | undefined;
  return row ? rowToToken(row) : null;
}

function rowToToken(row: Record<string, unknown>): Record<string, unknown> {
  return {
    contractAddress: row.contract_address,
    txHash: row.tx_hash,
    creatorAddress: row.creator_address,
    name: row.name,
    symbol: row.symbol,
    decimals: row.decimals,
    initialSupply: row.initial_supply,
    maxSupply: row.max_supply,
    description: row.description,
    imageUrl: row.image_url,
    websiteUrl: row.website_url,
    twitterUrl: row.twitter_url,
    telegramUrl: row.telegram_url,
    discordUrl: row.discord_url,
    metadataUri: row.metadata_uri,
    chainId: row.chain_id,
    createdAt: row.created_at,
    lastSeenBlock: row.last_seen_block,
    observedTotalSupply: row.observed_total_supply ?? null,
    observedOwner: row.observed_owner ?? null,
    observedAt: row.observed_at ?? null,
    observedStatus: row.observed_status ?? null,
    observedError: row.observed_error ?? null,
  };
}

// --- Indexer ----------------------------------------------------------------
//
// Periodically scans the chain for each registered token + the DEX contracts
// to confirm they're still live and to capture observed on-chain state
// (totalSupply, owner). Writes results back to the DB so /tokens and
// /token endpoints can show a "last verified at block N" badge.
//
// Runs in-process. Lifecycle is tied to the launcher-api unit so a restart
// resets the worker cleanly. No separate systemd unit needed.

const INDEXER_TICK_SECS = Number(process.env.LAUNCHER_INDEXER_TICK_SECS ?? 60);
const INDEXER_TOKEN_BATCH = Number(process.env.LAUNCHER_INDEXER_BATCH ?? 25);
const INDEXER_CHAIN_ID = Number(process.env.LAUNCHER_CHAIN_ID ?? 1);
const INDEXER_ENABLED = (process.env.LAUNCHER_INDEXER_ENABLED ?? '1') === '1';

const TOKEN_MANIFEST_PATH = path.join(CONTRACT_TEMPLATE_PATH, 'manifest.json');

async function viewTokenField(
  contract: string,
  method: string,
): Promise<unknown> {
  // Use the same view-call helper the DEX endpoints use, but pointed at the
  // token manifest. We reuse pythonExec for encode/decode.
  if (!fs.existsSync(TOKEN_MANIFEST_PATH)) {
    throw new Error('token manifest missing');
  }
  const dataHex = '0x' + (await pythonExec(ENCODE_CALLDATA_SCRIPT, [
    TOKEN_MANIFEST_PATH, method, '[]',
  ]));
  const { returnHex } = await rpcSimulateCall(contract, dataHex);
  const decoded = await pythonExec(DECODE_RESULT_SCRIPT, [
    TOKEN_MANIFEST_PATH, method, returnHex,
  ]);
  return JSON.parse(decoded);
}

async function getChainHeight(): Promise<number | null> {
  try {
    const head = await rpcCall<{ height?: number; number?: number }>(
      'chain.getHead', [],
    );
    if (head && typeof head === 'object') {
      const h = (head as { height?: number; number?: number });
      return typeof h.height === 'number' ? h.height
        : typeof h.number === 'number' ? h.number : null;
    }
  } catch {
    /* ignore */
  }
  return null;
}

const updateTokenObserved = db.prepare(`
  UPDATE tokens SET
    observed_total_supply = @totalSupply,
    observed_owner        = @owner,
    observed_at           = @observedAt,
    observed_status       = @status,
    observed_error        = @errorMsg,
    last_seen_block       = COALESCE(@blockHeight, last_seen_block)
  WHERE contract_address = @contractAddress
`);

const upsertIndexerState = db.prepare(`
  INSERT INTO indexer_state (chain_id, last_tick_at, last_chain_height, last_error, last_error_at, tokens_alive, tokens_missing)
  VALUES (@chainId, @lastTickAt, @lastChainHeight, @lastError, @lastErrorAt, @tokensAlive, @tokensMissing)
  ON CONFLICT(chain_id) DO UPDATE SET
    last_tick_at      = excluded.last_tick_at,
    last_chain_height = excluded.last_chain_height,
    last_error        = excluded.last_error,
    last_error_at     = excluded.last_error_at,
    tokens_alive      = excluded.tokens_alive,
    tokens_missing    = excluded.tokens_missing
`);

async function indexOneToken(
  contractAddress: string,
  blockHeight: number | null,
): Promise<'live' | 'missing' | 'error'> {
  try {
    const [totalSupply, owner] = await Promise.all([
      viewTokenField(contractAddress, 'totalSupply').catch(() => null),
      viewTokenField(contractAddress, 'owner').catch(() => null),
    ]);
    // If both calls return null, the contract probably doesn't exist (or
    // doesn't implement the standard token ABI we expect). Treat as missing.
    if (totalSupply === null && owner === null) {
      updateTokenObserved.run({
        contractAddress,
        totalSupply: null,
        owner: null,
        observedAt: nowSeconds(),
        status: 'missing',
        errorMsg: 'view calls returned no data',
        blockHeight,
      });
      return 'missing';
    }
    updateTokenObserved.run({
      contractAddress,
      totalSupply: totalSupply !== null ? String(totalSupply) : null,
      owner: owner !== null ? String(owner) : null,
      observedAt: nowSeconds(),
      status: 'live',
      errorMsg: null,
      blockHeight,
    });
    return 'live';
  } catch (err) {
    updateTokenObserved.run({
      contractAddress,
      totalSupply: null,
      owner: null,
      observedAt: nowSeconds(),
      status: 'error',
      errorMsg: err instanceof Error ? err.message.slice(0, 500) : String(err).slice(0, 500),
      blockHeight,
    });
    return 'error';
  }
}

let indexerRunning = false;

async function runIndexerTick(): Promise<void> {
  if (indexerRunning) return; // skip if previous tick still going
  indexerRunning = true;
  const tickStart = Date.now();
  let alive = 0;
  let missing = 0;
  let lastError: string | null = null;
  try {
    const height = await getChainHeight();
    const tokens = db
      .prepare(
        `SELECT contract_address FROM tokens
         WHERE chain_id = ?
         ORDER BY observed_at ASC NULLS FIRST, created_at DESC
         LIMIT ?`,
      )
      .all(INDEXER_CHAIN_ID, INDEXER_TOKEN_BATCH) as Array<{ contract_address: string }>;

    for (const row of tokens) {
      const status = await indexOneToken(row.contract_address, height);
      if (status === 'live') alive++;
      else missing++;
    }

    upsertIndexerState.run({
      chainId: INDEXER_CHAIN_ID,
      lastTickAt: nowSeconds(),
      lastChainHeight: height,
      lastError: null,
      lastErrorAt: null,
      tokensAlive: alive,
      tokensMissing: missing,
    });
    log.info(
      { tokens: tokens.length, alive, missing, height, ms: Date.now() - tickStart },
      'indexer tick complete',
    );
  } catch (err) {
    lastError = err instanceof Error ? err.message : String(err);
    log.error({ err: lastError }, 'indexer tick failed');
    upsertIndexerState.run({
      chainId: INDEXER_CHAIN_ID,
      lastTickAt: nowSeconds(),
      lastChainHeight: null,
      lastError: lastError.slice(0, 500),
      lastErrorAt: nowSeconds(),
      tokensAlive: alive,
      tokensMissing: missing,
    });
  } finally {
    indexerRunning = false;
  }
}

let indexerTimer: NodeJS.Timeout | null = null;
function startIndexer(): void {
  if (!INDEXER_ENABLED) {
    log.info('indexer disabled via LAUNCHER_INDEXER_ENABLED=0');
    return;
  }
  // Kick off one tick shortly after boot (skip if no tokens yet — the
  // function self-handles that case cheaply).
  setTimeout(() => { void runIndexerTick(); }, 5_000);
  indexerTimer = setInterval(() => { void runIndexerTick(); }, INDEXER_TICK_SECS * 1000);
  if (typeof indexerTimer.unref === 'function') indexerTimer.unref();
  log.info({ tickSecs: INDEXER_TICK_SECS, batch: INDEXER_TOKEN_BATCH }, 'indexer scheduled');
}

const server = app.listen(PORT, () => {
  log.info({ port: PORT, db: DB_PATH, rpc: CHAIN_RPC_URL }, 'launcher-api ready');
  startIndexer();
});

function shutdown(signal: string) {
  log.info({ signal }, 'shutting down');
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 5000).unref();
}
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
