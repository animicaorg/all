/**
 * Seed 002: Deposits Infrastructure
 * 
 * Seeds initial data for networks, assets, and asset_networks
 */

exports.seed = async function seed(knex) {
  // Insert networks
  const networks = [
    {
      id: "11111111-1111-1111-1111-111111111111",
      code: "BTC",
      name: "Bitcoin Mainnet",
      type: "UTXO",
      confirmations_required: 3,
      active: true,
      metadata: JSON.stringify({ explorer_url: "https://blockstream.info" })
    },
    {
      id: "22222222-2222-2222-2222-222222222222",
      code: "ETH",
      name: "Ethereum Mainnet",
      type: "EVM",
      confirmations_required: 12,
      active: true,
      metadata: JSON.stringify({ chain_id: 1, explorer_url: "https://etherscan.io" })
    },
    {
      id: "33333333-3333-3333-3333-333333333333",
      code: "ETH_SEPOLIA",
      name: "Ethereum Sepolia Testnet",
      type: "EVM",
      confirmations_required: 6,
      active: true,
      metadata: JSON.stringify({ chain_id: 11155111, explorer_url: "https://sepolia.etherscan.io" })
    }
  ];

  await knex("networks").insert(networks).onConflict("code").ignore();

  // Insert assets
  const assets = [
    {
      id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      symbol: "BTC",
      name: "Bitcoin",
      decimals: 8,
      active: true,
      metadata: JSON.stringify({})
    },
    {
      id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
      symbol: "ETH",
      name: "Ethereum",
      decimals: 18,
      active: true,
      metadata: JSON.stringify({})
    },
    {
      id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
      symbol: "USDT",
      name: "Tether USD",
      decimals: 6,
      active: true,
      metadata: JSON.stringify({})
    },
    {
      id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
      symbol: "USDC",
      name: "USD Coin",
      decimals: 6,
      active: true,
      metadata: JSON.stringify({})
    }
  ];

  await knex("assets").insert(assets).onConflict("symbol").ignore();

  // Insert asset_networks
  const assetNetworks = [
    {
      id: "ffffffff-0001-0001-0001-000000000001",
      asset_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", // BTC
      network_id: "11111111-1111-1111-1111-111111111111", // BTC network
      contract_address: null,
      bitgo_coin: "btc",
      deposits_enabled: true,
      withdrawals_enabled: true,
      min_deposit_atoms: "10000", // 0.0001 BTC
      confirmations_override: null,
      metadata: JSON.stringify({})
    },
    {
      id: "ffffffff-0002-0002-0002-000000000002",
      asset_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", // ETH
      network_id: "22222222-2222-2222-2222-222222222222", // ETH network
      contract_address: null,
      bitgo_coin: "eth",
      deposits_enabled: true,
      withdrawals_enabled: true,
      min_deposit_atoms: "1000000000000000", // 0.001 ETH
      confirmations_override: null,
      metadata: JSON.stringify({})
    },
    {
      id: "ffffffff-0003-0003-0003-000000000003",
      asset_id: "cccccccc-cccc-cccc-cccc-cccccccccccc", // USDT
      network_id: "22222222-2222-2222-2222-222222222222", // ETH network
      contract_address: "0xdac17f958d2ee523a2206206994597c13d831ec7",
      bitgo_coin: "erc20:usdt",
      deposits_enabled: true,
      withdrawals_enabled: true,
      min_deposit_atoms: "1000000", // 1 USDT
      confirmations_override: 6,
      metadata: JSON.stringify({})
    },
    {
      id: "ffffffff-0004-0004-0004-000000000004",
      asset_id: "dddddddd-dddd-dddd-dddd-dddddddddddd", // USDC
      network_id: "22222222-2222-2222-2222-222222222222", // ETH network
      contract_address: "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
      bitgo_coin: "erc20:usdc",
      deposits_enabled: true,
      withdrawals_enabled: true,
      min_deposit_atoms: "1000000", // 1 USDC
      confirmations_override: 6,
      metadata: JSON.stringify({})
    },
    {
      id: "ffffffff-0005-0005-0005-000000000005",
      asset_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", // ETH
      network_id: "33333333-3333-3333-3333-333333333333", // ETH Sepolia
      contract_address: null,
      bitgo_coin: "teth",
      deposits_enabled: true,
      withdrawals_enabled: true,
      min_deposit_atoms: "1000000000000000", // 0.001 ETH
      confirmations_override: null,
      metadata: JSON.stringify({ testnet: true })
    }
  ];

  await knex("asset_networks")
    .insert(assetNetworks)
    .onConflict(["asset_id", "network_id", "contract_address"])
    .ignore();
};
