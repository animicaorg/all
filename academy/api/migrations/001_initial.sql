-- Academy database schema.
--
-- Tutorials/steps are seeded by the server from a static manifest on
-- boot — we keep the manifest in code, not in the database, so the
-- catalog ships with the deployment and stays version-pinned.
--
-- The mutable state is: per-wallet progress, awarded badges, redeemed
-- claim nonces, and recorded deposits.

CREATE TABLE tutorials (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  track TEXT NOT NULL,
  difficulty TEXT NOT NULL,
  reward_anim TEXT NOT NULL,
  estimated_minutes INTEGER NOT NULL,
  step_count INTEGER NOT NULL,
  updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE tutorial_steps (
  tutorial_id TEXT NOT NULL REFERENCES tutorials(id) ON DELETE CASCADE,
  step_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  title TEXT NOT NULL,
  reward_anim TEXT NOT NULL,
  PRIMARY KEY (tutorial_id, step_id)
);

CREATE TABLE progress (
  address TEXT NOT NULL,
  tutorial_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  completed_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
  signature_hex TEXT,
  PRIMARY KEY (address, tutorial_id, step_id)
);

CREATE INDEX idx_progress_address ON progress(address);
CREATE INDEX idx_progress_tutorial ON progress(tutorial_id);

CREATE TABLE claim_nonces (
  nonce TEXT PRIMARY KEY,
  address TEXT NOT NULL,
  tutorial_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  issued_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
  redeemed_at INTEGER,
  payout_tx_hash TEXT
);

CREATE INDEX idx_nonces_address ON claim_nonces(address);

CREATE TABLE achievements (
  address TEXT NOT NULL,
  badge_id TEXT NOT NULL,
  awarded_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
  reward_anim TEXT NOT NULL,
  payout_tx_hash TEXT,
  PRIMARY KEY (address, badge_id)
);

CREATE TABLE deposits (
  tx_hash TEXT PRIMARY KEY,
  address TEXT NOT NULL,
  amount_anim TEXT NOT NULL,
  recorded_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX idx_deposits_address ON deposits(address);

CREATE TABLE pool_stats (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

INSERT INTO pool_stats (key, value) VALUES
  ('balance_base_units', '0'),
  ('paid_out_base_units', '0'),
  ('pending_claims_base_units', '0');
