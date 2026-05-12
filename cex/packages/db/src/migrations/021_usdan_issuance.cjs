const ANIMICA_NETWORK_ID = "44444444-4444-4444-4444-444444444444";
const USDAN_ASSET_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const USDAN_ASSET_NETWORK_ID = "ffffffff-0010-0010-0010-000000000010";

const TOKEN_CONTRACT_ADDRESS = "0x29a354f5eff588abbfb2c06c0786fdf118567df50f074b5cc59e09ee00d16059";
const MINT_CONTROLLER_ADDRESS = "0x9e4b55d54cf8629b2a1af0e9add0e477de34b405e9bb807157073e2a08314a69";
const REDEMPTION_CONTROLLER_ADDRESS = "0xfb9b220ed358c1aee5cec1bffcf754206c71f77be7e422e688e7e2e9bb905eee";
const COMPLIANCE_CONTROLLER_ADDRESS = "0xa23ba4895937aa80ef79db23e6e66a5de432a9d4cd474d5c0d5d98188def82ab";
const RESERVE_ATTESTATION_ADDRESS = "0x2c40d64714f5744d21b88a28d4468961c6a19f2ed44f099b35177172c6c613c1";

const USDAN_MARKETS = [
  {
    symbol: "ANM-USDAN",
    base_asset: "ANM",
    quote_asset: "USDAN",
    price_tick: "0.0001",
    size_step: "0.000000001",
    min_order_size: "1",
  },
  {
    symbol: "BTC-USDAN",
    base_asset: "BTC",
    quote_asset: "USDAN",
    price_tick: "0.01",
    size_step: "0.00000001",
    min_order_size: "0.00001",
  },
  {
    symbol: "DOGE-USDAN",
    base_asset: "DOGE",
    quote_asset: "USDAN",
    price_tick: "0.0001",
    size_step: "0.00000001",
    min_order_size: "1",
  },
  {
    symbol: "LTC-USDAN",
    base_asset: "LTC",
    quote_asset: "USDAN",
    price_tick: "0.01",
    size_step: "0.00000001",
    min_order_size: "0.001",
  },
];

exports.up = async function up(knex) {
  await knex("networks")
    .insert({
      id: ANIMICA_NETWORK_ID,
      code: "ANIMICA",
      name: "Animica Mainnet",
      type: "ACCOUNT",
      confirmations_required: 20,
      active: true,
      metadata: JSON.stringify({
        chain_id: 1337,
        rpc_url: "http://127.0.0.1:8545/rpc",
        explorer_url: "https://explorer.animica.org",
      }),
    })
    .onConflict("code")
    .merge(["name", "type", "confirmations_required", "active", "metadata"]);

  await knex("assets")
    .insert({
      id: USDAN_ASSET_ID,
      symbol: "USDAN",
      name: "USDAN",
      decimals: 6,
      active: true,
      metadata: JSON.stringify({
        canonical: true,
        stablecoin: true,
        token_standard: "ANIMICA_TOKEN",
        contract_package: "contracts/packages/usdan_token",
        deployment_record: "contracts/build/usdan/deployments/local.json",
        public_config_path: "/usdan/config",
        token_contract_address: TOKEN_CONTRACT_ADDRESS,
      }),
    })
    .onConflict("symbol")
    .merge(["name", "decimals", "active", "metadata"]);

  await knex("asset_networks")
    .insert({
      id: USDAN_ASSET_NETWORK_ID,
      asset_id: knex.raw("(SELECT id FROM assets WHERE symbol = 'USDAN')"),
      network_id: ANIMICA_NETWORK_ID,
      contract_address: TOKEN_CONTRACT_ADDRESS,
      bitgo_coin: null,
      deposits_enabled: true,
      withdrawals_enabled: true,
      min_deposit_atoms: "1000000",
      confirmations_override: null,
      metadata: JSON.stringify({
        provider: "ANIMICA_NODE",
        canonical: true,
        display_symbol: "USDAN",
        explorer_slug: "usdan",
        token_standard: "ANIMICA_TOKEN",
        deployment_record: "contracts/build/usdan/deployments/local.json",
        public_config_path: "/usdan/config",
        token_contract_address: TOKEN_CONTRACT_ADDRESS,
        mint_controller_address: MINT_CONTROLLER_ADDRESS,
        redemption_controller_address: REDEMPTION_CONTROLLER_ADDRESS,
        compliance_controller_address: COMPLIANCE_CONTROLLER_ADDRESS,
        reserve_attestation_address: RESERVE_ATTESTATION_ADDRESS,
        flat_withdrawal_fee_atoms: "100000",
        flat_withdrawal_fee: "0.10",
      }),
    })
    .onConflict("id")
    .merge([
      "asset_id",
      "network_id",
      "contract_address",
      "bitgo_coin",
      "deposits_enabled",
      "withdrawals_enabled",
      "min_deposit_atoms",
      "confirmations_override",
      "metadata",
    ]);

  await knex.raw(`
    CREATE TABLE IF NOT EXISTS usdan_config (
      id varchar PRIMARY KEY DEFAULT 'default',
      symbol varchar NOT NULL DEFAULT 'USDAN',
      name varchar NOT NULL DEFAULT 'USDAN',
      decimals integer NOT NULL DEFAULT 6,
      network_code varchar NOT NULL DEFAULT 'ANIMICA',
      asset_network_id uuid REFERENCES asset_networks(id),
      token_address varchar,
      mint_controller_address varchar,
      redemption_controller_address varchar,
      compliance_controller_address varchar,
      reserve_attestation_address varchar,
      exchange_custody_address varchar,
      issuance_paused boolean NOT NULL DEFAULT false,
      redemptions_paused boolean NOT NULL DEFAULT false,
      card_purchases_paused boolean NOT NULL DEFAULT false,
      max_total_supply_atoms numeric(30,0) NOT NULL DEFAULT 1000000000000000,
      daily_mint_cap_atoms numeric(30,0) NOT NULL DEFAULT 100000000000000,
      daily_redeem_cap_atoms numeric(30,0) NOT NULL DEFAULT 100000000000000,
      min_card_purchase_cents integer NOT NULL DEFAULT 500,
      max_card_purchase_cents integer NOT NULL DEFAULT 1000000,
      per_user_daily_card_limit_cents integer NOT NULL DEFAULT 2500000,
      card_fee_bps integer NOT NULL DEFAULT 290,
      card_fee_fixed_cents integer NOT NULL DEFAULT 30,
      manual_review_threshold_cents integer NOT NULL DEFAULT 1000000,
      reserve_balance_cents integer NOT NULL DEFAULT 0,
      latest_reconciliation_at timestamptz,
      metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
      created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
  `);

  await knex.raw(`
    INSERT INTO usdan_config (
      id,
      asset_network_id,
      token_address,
      mint_controller_address,
      redemption_controller_address,
      compliance_controller_address,
      reserve_attestation_address,
      metadata
    )
    VALUES (
      'default',
      ?::uuid,
      ?,
      ?,
      ?,
      ?,
      ?,
      ?::jsonb
    )
    ON CONFLICT (id)
    DO UPDATE SET
      asset_network_id = EXCLUDED.asset_network_id,
      token_address = EXCLUDED.token_address,
      mint_controller_address = EXCLUDED.mint_controller_address,
      redemption_controller_address = EXCLUDED.redemption_controller_address,
      compliance_controller_address = EXCLUDED.compliance_controller_address,
      reserve_attestation_address = EXCLUDED.reserve_attestation_address,
      metadata = usdan_config.metadata || EXCLUDED.metadata,
      updated_at = NOW()
  `, [
    USDAN_ASSET_NETWORK_ID,
    TOKEN_CONTRACT_ADDRESS,
    MINT_CONTROLLER_ADDRESS,
    REDEMPTION_CONTROLLER_ADDRESS,
    COMPLIANCE_CONTROLLER_ADDRESS,
    RESERVE_ATTESTATION_ADDRESS,
    JSON.stringify({ deployment_record: "contracts/build/usdan/deployments/local.json" }),
  ]);

  await knex.raw(`
    CREATE TABLE IF NOT EXISTS usdan_accounts (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid REFERENCES users(id),
      wallet_address varchar,
      account_type varchar NOT NULL DEFAULT 'USER',
      treasury_counterparty_id varchar,
      status varchar NOT NULL DEFAULT 'ACTIVE',
      metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
      created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE (user_id, wallet_address)
    )
  `);

  await knex.raw(`
    CREATE TABLE IF NOT EXISTS usdan_card_purchase_requests (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid NOT NULL REFERENCES users(id),
      wallet_address varchar NOT NULL,
      destination_type varchar NOT NULL,
      amount_atoms numeric(30,0) NOT NULL,
      usd_amount_cents integer NOT NULL,
      fee_cents integer NOT NULL DEFAULT 0,
      net_usd_cents integer NOT NULL,
      funding_source_type varchar NOT NULL DEFAULT 'CARD',
      status varchar NOT NULL DEFAULT 'CREATED',
      external_reference varchar NOT NULL UNIQUE,
      treasury_reference varchar,
      card_provider_reference varchar,
      chain_tx_hash varchar,
      idempotency_key varchar NOT NULL,
      failure_reason varchar,
      metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
      created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
      completed_at timestamptz,
      UNIQUE (user_id, idempotency_key)
    )
  `);

  await knex.raw(`
    CREATE TABLE IF NOT EXISTS usdan_mint_requests (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid REFERENCES users(id),
      card_purchase_id uuid REFERENCES usdan_card_purchase_requests(id),
      wallet_address varchar NOT NULL,
      destination_type varchar NOT NULL DEFAULT 'EXTERNAL_WALLET',
      amount_atoms numeric(30,0) NOT NULL,
      usd_amount_cents integer NOT NULL,
      funding_source_type varchar NOT NULL DEFAULT 'TREASURY',
      status varchar NOT NULL DEFAULT 'CREATED',
      external_reference varchar NOT NULL UNIQUE,
      treasury_reference varchar,
      chain_tx_hash varchar,
      idempotency_key varchar NOT NULL,
      failure_reason varchar,
      metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
      created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
      completed_at timestamptz,
      UNIQUE (user_id, idempotency_key)
    )
  `);

  await knex.raw(`
    CREATE TABLE IF NOT EXISTS usdan_reserve_events (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      event_type varchar NOT NULL,
      amount_cents integer NOT NULL,
      status varchar NOT NULL,
      provider varchar NOT NULL DEFAULT 'MODERN_TREASURY',
      treasury_reference varchar,
      external_reference varchar,
      request_type varchar,
      request_id uuid,
      idempotency_key varchar NOT NULL UNIQUE,
      metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
      created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
  `);

  await knex.raw(`
    CREATE TABLE IF NOT EXISTS usdan_ledger_events (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      entry_group_id uuid NOT NULL,
      event_type varchar NOT NULL,
      account_key varchar NOT NULL,
      user_id uuid REFERENCES users(id),
      request_type varchar,
      request_id uuid,
      asset varchar NOT NULL DEFAULT 'USDAN',
      amount_atoms numeric(30,0) NOT NULL,
      direction varchar NOT NULL,
      idempotency_key varchar NOT NULL UNIQUE,
      metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
      created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
  `);

  await knex.raw(`
    CREATE TABLE IF NOT EXISTS usdan_compliance_flags (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid REFERENCES users(id),
      wallet_address varchar,
      flag_type varchar NOT NULL,
      status varchar NOT NULL DEFAULT 'OPEN',
      reason varchar NOT NULL,
      request_id uuid,
      request_type varchar,
      created_by uuid REFERENCES users(id),
      resolved_by uuid REFERENCES users(id),
      metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
      created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
      resolved_at timestamptz
    )
  `);

  await knex.raw(`
    CREATE TABLE IF NOT EXISTS usdan_webhook_events (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      provider varchar NOT NULL,
      event_id varchar NOT NULL,
      event_type varchar NOT NULL,
      status varchar NOT NULL DEFAULT 'RECEIVED',
      raw_body_hash varchar,
      payload jsonb NOT NULL,
      failure_reason varchar,
      received_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
      processed_at timestamptz,
      UNIQUE (provider, event_id)
    )
  `);

  await knex("markets")
    .insert(
      USDAN_MARKETS.map((market) => ({
        id: knex.raw("gen_random_uuid()"),
        ...market,
        active: true,
        maker_fee_bps: 10,
        taker_fee_bps: 20,
        fee_asset: "USDAN",
      }))
    )
    .onConflict("symbol")
    .merge([
      "base_asset",
      "quote_asset",
      "active",
      "price_tick",
      "size_step",
      "min_order_size",
      "maker_fee_bps",
      "taker_fee_bps",
      "fee_asset",
    ]);

  const hasMarketSequence = await knex.schema.hasTable("market_sequence");
  if (hasMarketSequence) {
    await knex.raw(
      `
        INSERT INTO market_sequence (market_id, last_seq)
        SELECT id, 0
        FROM markets
        WHERE symbol = ANY(?::text[])
        ON CONFLICT (market_id) DO NOTHING
      `,
      [USDAN_MARKETS.map((market) => market.symbol)]
    );
  }
};

exports.down = async function down(knex) {
  await knex("markets")
    .whereIn(
      "symbol",
      USDAN_MARKETS.map((market) => market.symbol)
    )
    .update({ active: false });

  await knex.schema.dropTableIfExists("usdan_webhook_events");
  await knex.schema.dropTableIfExists("usdan_compliance_flags");
  await knex.schema.dropTableIfExists("usdan_ledger_events");
  await knex.schema.dropTableIfExists("usdan_reserve_events");
  await knex.schema.dropTableIfExists("usdan_mint_requests");
  await knex.schema.dropTableIfExists("usdan_card_purchase_requests");
  await knex.schema.dropTableIfExists("usdan_accounts");
  await knex.schema.dropTableIfExists("usdan_config");

  await knex("asset_networks").where({ id: USDAN_ASSET_NETWORK_ID }).delete();
  await knex("assets").where({ symbol: "USDAN" }).delete();
};
