PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS tx_classification (
  tx_hash TEXT PRIMARY KEY,
  tx_type TEXT NOT NULL,
  failed INTEGER NOT NULL DEFAULT 0,
  is_reverted INTEGER NOT NULL DEFAULT 0,
  reason TEXT,
  from_address TEXT,
  to_address TEXT,
  created_contract_address TEXT,
  method_selector TEXT,
  raw_input TEXT,
  decoded_call_json TEXT,
  decoded_events_json TEXT,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS contract_profile (
  address TEXT PRIMARY KEY,
  account_type TEXT NOT NULL,
  creator_address TEXT,
  creator_tx_hash TEXT,
  creation_block_height INTEGER,
  creation_block_hash TEXT,
  creation_timestamp INTEGER,
  code_hash TEXT,
  runtime_code_hash TEXT,
  code_size_bytes INTEGER,
  metadata_json TEXT,
  abi_json TEXT,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_job (
  job_id TEXT PRIMARY KEY,
  address TEXT NOT NULL,
  status TEXT NOT NULL,
  request_json TEXT NOT NULL,
  result_json TEXT,
  error_message TEXT,
  submitted_at INTEGER NOT NULL,
  completed_at INTEGER,
  FOREIGN KEY(address) REFERENCES contract_profile(address) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_contract_profile_creator_tx ON contract_profile(creator_tx_hash);
CREATE INDEX IF NOT EXISTS idx_verification_job_address ON verification_job(address);
CREATE INDEX IF NOT EXISTS idx_verification_job_status ON verification_job(status);
