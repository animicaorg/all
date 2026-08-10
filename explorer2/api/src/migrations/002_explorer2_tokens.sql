-- Token tracker: ANM-20 (AnimicaTokenStandard) index + promos + price history.

CREATE TABLE IF NOT EXISTS token_profile (
  address TEXT PRIMARY KEY,          -- bech32m display address (alg_id 0x0000 contract encoding)
  addr_key TEXT NOT NULL UNIQUE,     -- canonical 32-byte digest key (lowercase 0x-hex)
  kind TEXT NOT NULL DEFAULT 'token',-- token | dex_pair | dex_router | dex_factory
  name TEXT,
  symbol TEXT,
  decimals INTEGER,
  metadata_uri TEXT,
  image_url TEXT,
  description TEXT,
  links_json TEXT,
  total_supply TEXT,                 -- base units, decimal string (BigInt-safe)
  price_anm REAL,
  liquidity_anm REAL,
  change_24h REAL,
  pair_address TEXT,
  fee_bps INTEGER,
  initial_supply TEXT,
  max_supply TEXT,
  mintable INTEGER,
  creator TEXT,
  creation_height INTEGER,
  creation_tx TEXT,
  creation_ts INTEGER,
  init_tx TEXT,
  promoted INTEGER NOT NULL DEFAULT 0,
  promo_days_left INTEGER,
  meta_fetched_at INTEGER,
  updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_token_profile_kind ON token_profile(kind);
CREATE INDEX IF NOT EXISTS idx_token_profile_symbol ON token_profile(symbol);

-- Every contract deployment seen by the token scanner (any kind). Used to
-- backfill creator/creation info onto tokens whose init call was indexed
-- before (or without) their deploy tx.
CREATE TABLE IF NOT EXISTS token_deploy (
  addr_key TEXT PRIMARY KEY,
  address TEXT,
  creator TEXT,
  creation_height INTEGER,
  creation_tx TEXT,
  creation_ts INTEGER,
  code_hash TEXT,
  manifest_name TEXT,
  updated_at INTEGER NOT NULL
);

-- Staged init calls (mined, possibly reverted). Init calldata is applied to a
-- token profile ONLY after verification against token_deploy: the target must
-- have a recorded deploy and the init sender must equal the recorded deployer.
-- This blocks metadata spoofing — anyone can broadcast init-shaped calldata at
-- any address, but only the deployer's init describes the token.
CREATE TABLE IF NOT EXISTS token_init_seen (
  tx_hash TEXT PRIMARY KEY,
  addr_key TEXT NOT NULL,
  sender_key TEXT,
  kind TEXT NOT NULL DEFAULT 'token',
  height INTEGER,
  name TEXT,
  symbol TEXT,
  decimals INTEGER,
  initial_supply TEXT,
  max_supply TEXT,
  mintable INTEGER,
  metadata_uri TEXT,
  updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_token_init_seen_addr ON token_init_seen(addr_key);

-- ANMPROMO1 promotions: plain transfers to the foundation treasury whose data
-- decodes to "ANMPROMO1|<contract>|<days>|<label>". Windows are ADDITIVE: a
-- deposit made while a promo is running extends the run from its end, so total
-- featured time equals the sum of purchased days (chained at read time).
CREATE TABLE IF NOT EXISTS token_promo (
  tx_hash TEXT PRIMARY KEY,
  addr_key TEXT NOT NULL,
  start_ts INTEGER NOT NULL,
  days INTEGER NOT NULL,
  label TEXT,
  updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_token_promo_addr ON token_promo(addr_key);

-- Price history, written whenever a live pool price is successfully read.
CREATE TABLE IF NOT EXISTS token_price_point (
  address TEXT NOT NULL,
  t INTEGER NOT NULL,
  price_anm REAL NOT NULL,
  PRIMARY KEY (address, t)
);

-- Scanner cursors (last forward-scanned height, backfill cursor, …).
CREATE TABLE IF NOT EXISTS token_scan_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
