CREATE EXTENSION IF NOT EXISTS "pgcrypto";

INSERT INTO networks (id, code, name, type, confirmations_required, active, metadata)
VALUES
  ('11111111-1111-1111-1111-111111111111', 'BTC', 'Bitcoin Mainnet', 'UTXO', 3, true, '{"explorer_url":"https://blockstream.info"}'::jsonb),
  ('22222222-2222-2222-2222-222222222222', 'ETH', 'Ethereum Mainnet', 'EVM', 12, true, '{"chain_id":1,"explorer_url":"https://etherscan.io"}'::jsonb),
  ('55555555-5555-5555-5555-555555555555', 'SOL', 'Solana Mainnet', 'SOLANA', 32, false, '{"explorer_url":"https://solscan.io"}'::jsonb),
  ('66666666-6666-6666-6666-666666666666', 'LTC', 'Litecoin Mainnet', 'UTXO', 6, true, '{"explorer_url":"https://blockchair.com/litecoin"}'::jsonb),
  ('77777777-7777-7777-7777-777777777777', 'DOGE', 'Dogecoin Mainnet', 'UTXO', 20, true, '{"explorer_url":"https://blockchair.com/dogecoin"}'::jsonb),
  ('88888888-8888-8888-8888-888888888888', 'ZEC', 'Zcash Mainnet', 'UTXO', 24, true, '{"explorer_url":"https://blockchair.com/zcash"}'::jsonb),
  ('44444444-4444-4444-4444-444444444444', 'ANIMICA', 'Animica Mainnet', 'ACCOUNT', 20, true, '{"chain_id":1337,"rpc_url":"http://127.0.0.1:8545/rpc","explorer_url":"https://explorer.animica.org"}'::jsonb)
ON CONFLICT (code) DO UPDATE SET
  name = EXCLUDED.name,
  type = EXCLUDED.type,
  confirmations_required = EXCLUDED.confirmations_required,
  active = EXCLUDED.active,
  metadata = networks.metadata || EXCLUDED.metadata;

INSERT INTO assets (id, symbol, name, decimals, active, metadata)
VALUES
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'BTC', 'Bitcoin', 8, true, '{}'::jsonb),
  ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'ETH', 'Ethereum', 18, false, '{}'::jsonb),
  ('99999999-9999-9999-9999-999999999999', 'SOL', 'Solana', 9, false, '{}'::jsonb),
  ('abababab-abab-abab-abab-abababababab', 'LTC', 'Litecoin', 8, true, '{}'::jsonb),
  ('cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd', 'DOGE', 'Dogecoin', 8, true, '{}'::jsonb),
  ('efefefef-efef-efef-efef-efefefefefef', 'ZEC', 'Zcash', 8, true, '{}'::jsonb),
  ('eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee', 'ANM', 'Animica', 9, true, '{"native":true}'::jsonb)
ON CONFLICT (symbol) DO UPDATE SET
  name = EXCLUDED.name,
  decimals = EXCLUDED.decimals,
  active = EXCLUDED.active,
  metadata = assets.metadata || EXCLUDED.metadata;

INSERT INTO asset_networks (
  id,
  asset_id,
  network_id,
  contract_address,
  bitgo_coin,
  deposits_enabled,
  withdrawals_enabled,
  min_deposit_atoms,
  confirmations_override,
  metadata
)
VALUES
  (
    'ffffffff-0001-0001-0001-000000000001',
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
    '11111111-1111-1111-1111-111111111111',
    NULL,
    'btc',
    true,
    true,
    '10000',
    NULL,
    '{"provider":"BITGO","flat_withdrawal_fee_atoms":"5000","flat_withdrawal_fee":"0.00005"}'::jsonb
  ),
  (
    'ffffffff-0002-0002-0002-000000000002',
    'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
    '22222222-2222-2222-2222-222222222222',
    NULL,
    'eth',
    false,
    false,
    '1000000',
    NULL,
    '{"provider":"BITGO","flat_withdrawal_fee_atoms":"3000000000000000","flat_withdrawal_fee":"0.003"}'::jsonb
  ),
  (
    'ffffffff-0007-0007-0007-000000000007',
    '99999999-9999-9999-9999-999999999999',
    '55555555-5555-5555-5555-555555555555',
    NULL,
    'sol',
    false,
    false,
    '10000000',
    NULL,
    '{"provider":"BITGO","flat_withdrawal_fee_atoms":"10000000","flat_withdrawal_fee":"0.01"}'::jsonb
  ),
  (
    'ffffffff-0008-0008-0008-000000000008',
    'abababab-abab-abab-abab-abababababab',
    '66666666-6666-6666-6666-666666666666',
    NULL,
    'ltc',
    true,
    true,
    '100000',
    NULL,
    '{"provider":"BITGO","flat_withdrawal_fee_atoms":"10000","flat_withdrawal_fee":"0.0001"}'::jsonb
  ),
  (
    'ffffffff-0009-0009-0009-000000000009',
    'cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd',
    '77777777-7777-7777-7777-777777777777',
    NULL,
    'doge',
    true,
    true,
    '1000000000',
    NULL,
    '{"provider":"BITGO","flat_withdrawal_fee_atoms":"100000000","flat_withdrawal_fee":"1"}'::jsonb
  ),
  (
    'ffffffff-000a-000a-000a-00000000000a',
    'efefefef-efef-efef-efef-efefefefefef',
    '88888888-8888-8888-8888-888888888888',
    NULL,
    'zec',
    true,
    true,
    '100000',
    NULL,
    '{"provider":"BITGO","flat_withdrawal_fee_atoms":"10000","flat_withdrawal_fee":"0.0001"}'::jsonb
  ),
  (
    'ffffffff-0006-0006-0006-000000000006',
    'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee',
    '44444444-4444-4444-4444-444444444444',
    NULL,
    NULL,
    true,
    true,
    '1000000',
    NULL,
    '{"provider":"ANIMICA_NODE","flat_withdrawal_fee_atoms":"1000000000","flat_withdrawal_fee":"1"}'::jsonb
  )
ON CONFLICT (id) DO UPDATE SET
  asset_id = EXCLUDED.asset_id,
  network_id = EXCLUDED.network_id,
  contract_address = EXCLUDED.contract_address,
  bitgo_coin = EXCLUDED.bitgo_coin,
  deposits_enabled = EXCLUDED.deposits_enabled,
  withdrawals_enabled = EXCLUDED.withdrawals_enabled,
  min_deposit_atoms = EXCLUDED.min_deposit_atoms,
  confirmations_override = EXCLUDED.confirmations_override,
  metadata = asset_networks.metadata || EXCLUDED.metadata;

INSERT INTO withdrawal_policies (
  asset_network_id,
  min_withdrawal_atoms,
  required_approvals,
  high_risk_approvals,
  enabled,
  metadata
)
VALUES
  (
    'ffffffff-0001-0001-0001-000000000001',
    '10000',
    1,
    2,
    true,
    '{"withdrawalFeeAtoms":"5000","withdrawalFee":"0.00005","flatFee":true,"feeAsset":"BTC","rationale":"Lower flat BTC withdrawal fee and minimum for the current product policy."}'::jsonb
  ),
  (
    'ffffffff-0002-0002-0002-000000000002',
    '10000000000000000',
    1,
    2,
    false,
    '{"withdrawalFeeAtoms":"3000000000000000","withdrawalFee":"0.003","flatFee":true,"feeAsset":"ETH","rationale":"Flat fee includes gas headroom plus an operating margin."}'::jsonb
  ),
  (
    'ffffffff-0007-0007-0007-000000000007',
    '100000000',
    1,
    2,
    false,
    '{"withdrawalFeeAtoms":"10000000","withdrawalFee":"0.01","flatFee":true,"feeAsset":"SOL","rationale":"Flat fee covers Solana network fees, BitGo operations, and exchange margin."}'::jsonb
  ),
  (
    'ffffffff-0008-0008-0008-000000000008',
    '100000',
    1,
    2,
    true,
    '{"withdrawalFeeAtoms":"10000","withdrawalFee":"0.0001","flatFee":true,"feeAsset":"LTC","rationale":"Flat fee for Litecoin BitGo withdrawals."}'::jsonb
  ),
  (
    'ffffffff-0009-0009-0009-000000000009',
    '1000000000',
    1,
    2,
    true,
    '{"withdrawalFeeAtoms":"100000000","withdrawalFee":"1","flatFee":true,"feeAsset":"DOGE","rationale":"Flat fee for Dogecoin BitGo withdrawals."}'::jsonb
  ),
  (
    'ffffffff-000a-000a-000a-00000000000a',
    '100000',
    1,
    2,
    true,
    '{"withdrawalFeeAtoms":"10000","withdrawalFee":"0.0001","flatFee":true,"feeAsset":"ZEC","rationale":"Flat fee for Zcash BitGo withdrawals."}'::jsonb
  ),
  (
    'ffffffff-0006-0006-0006-000000000006',
    '10000000000',
    1,
    2,
    true,
    '{"withdrawalFeeAtoms":"1000000000","withdrawalFee":"1","flatFee":true,"feeAsset":"ANM","rationale":"Flat fee covers Animica node transaction cost and exchange operations."}'::jsonb
  )
ON CONFLICT (asset_network_id) DO UPDATE SET
  min_withdrawal_atoms = EXCLUDED.min_withdrawal_atoms,
  required_approvals = EXCLUDED.required_approvals,
  high_risk_approvals = EXCLUDED.high_risk_approvals,
  enabled = EXCLUDED.enabled,
  metadata = withdrawal_policies.metadata || EXCLUDED.metadata,
  updated_at = NOW();

INSERT INTO markets (
  id,
  symbol,
  base_asset,
  quote_asset,
  active,
  price_tick,
  size_step,
  min_order_size,
  maker_fee_bps,
  taker_fee_bps,
  fee_asset
)
VALUES
  (gen_random_uuid(), 'BTC-ANM', 'BTC', 'ANM', true, '0.00000001', '0.00000001', '0.0001', 10, 20, 'ANM'),
  (gen_random_uuid(), 'ETH-ANM', 'ETH', 'ANM', false, '0.00000001', '0.00000001', '0.001', 10, 20, 'ANM'),
  (gen_random_uuid(), 'SOL-ANM', 'SOL', 'ANM', false, '0.00000001', '0.00000001', '0.01', 10, 20, 'ANM'),
  (gen_random_uuid(), 'LTC-ANM', 'LTC', 'ANM', true, '0.00000001', '0.00000001', '0.001', 10, 20, 'ANM'),
  (gen_random_uuid(), 'DOGE-ANM', 'DOGE', 'ANM', true, '0.00000001', '0.00000001', '10', 10, 20, 'ANM'),
  (gen_random_uuid(), 'ZEC-ANM', 'ZEC', 'ANM', true, '0.00000001', '0.00000001', '0.001', 10, 20, 'ANM')
ON CONFLICT (symbol) DO UPDATE SET
  base_asset = EXCLUDED.base_asset,
  quote_asset = EXCLUDED.quote_asset,
  active = EXCLUDED.active,
  price_tick = EXCLUDED.price_tick,
  size_step = EXCLUDED.size_step,
  min_order_size = EXCLUDED.min_order_size,
  maker_fee_bps = EXCLUDED.maker_fee_bps,
  taker_fee_bps = EXCLUDED.taker_fee_bps,
  fee_asset = EXCLUDED.fee_asset;

INSERT INTO market_sequence (market_id, last_seq)
SELECT id, 0
FROM markets
WHERE symbol IN ('BTC-ANM', 'LTC-ANM', 'DOGE-ANM', 'ZEC-ANM')
ON CONFLICT (market_id) DO NOTHING;
