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
      id: "55555555-5555-5555-5555-555555555555",
      code: "SOL",
      name: "Solana Mainnet",
      type: "SOLANA",
      confirmations_required: 32,
      active: false,
      metadata: JSON.stringify({ explorer_url: "https://solscan.io" })
    },
    {
      id: "66666666-6666-6666-6666-666666666666",
      code: "LTC",
      name: "Litecoin Mainnet",
      type: "UTXO",
      confirmations_required: 6,
      active: true,
      metadata: JSON.stringify({ explorer_url: "https://blockchair.com/litecoin" })
    },
    {
      id: "77777777-7777-7777-7777-777777777777",
      code: "DOGE",
      name: "Dogecoin Mainnet",
      type: "UTXO",
      confirmations_required: 20,
      active: true,
      metadata: JSON.stringify({ explorer_url: "https://blockchair.com/dogecoin" })
    },
    {
      id: "88888888-8888-8888-8888-888888888888",
      code: "ZEC",
      name: "Zcash Mainnet",
      type: "UTXO",
      confirmations_required: 24,
      active: true,
      metadata: JSON.stringify({ explorer_url: "https://blockchair.com/zcash" })
    },
    {
      id: "33333333-3333-3333-3333-333333333333",
      code: "ETH_SEPOLIA",
      name: "Ethereum Sepolia Testnet",
      type: "EVM",
      confirmations_required: 6,
      active: false,
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
      active: false,
      metadata: JSON.stringify({})
    },
    {
      id: "99999999-9999-9999-9999-999999999999",
      symbol: "SOL",
      name: "Solana",
      decimals: 9,
      active: false,
      metadata: JSON.stringify({})
    },
    {
      id: "abababab-abab-abab-abab-abababababab",
      symbol: "LTC",
      name: "Litecoin",
      decimals: 8,
      active: true,
      metadata: JSON.stringify({})
    },
    {
      id: "cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd",
      symbol: "DOGE",
      name: "Dogecoin",
      decimals: 8,
      active: true,
      metadata: JSON.stringify({})
    },
    {
      id: "efefefef-efef-efef-efef-efefefefefef",
      symbol: "ZEC",
      name: "Zcash",
      decimals: 8,
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
      metadata: JSON.stringify({
        provider: "BITGO",
        flat_withdrawal_fee_atoms: "5000",
        flat_withdrawal_fee: "0.00005"
      })
    },
    {
      id: "ffffffff-0002-0002-0002-000000000002",
      asset_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", // ETH
      network_id: "22222222-2222-2222-2222-222222222222", // ETH network
      contract_address: null,
      bitgo_coin: "eth",
      deposits_enabled: false,
      withdrawals_enabled: false,
      min_deposit_atoms: "1000000000000000", // 0.001 ETH
      confirmations_override: null,
      metadata: JSON.stringify({
        provider: "BITGO",
        flat_withdrawal_fee_atoms: "3000000000000000",
        flat_withdrawal_fee: "0.003"
      })
    },
    {
      id: "ffffffff-0007-0007-0007-000000000007",
      asset_id: "99999999-9999-9999-9999-999999999999", // SOL
      network_id: "55555555-5555-5555-5555-555555555555", // SOL network
      contract_address: null,
      bitgo_coin: "sol",
      deposits_enabled: false,
      withdrawals_enabled: false,
      min_deposit_atoms: "10000000", // 0.01 SOL
      confirmations_override: null,
      metadata: JSON.stringify({
        provider: "BITGO",
        flat_withdrawal_fee_atoms: "10000000",
        flat_withdrawal_fee: "0.01"
      })
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
      id: "ffffffff-0008-0008-0008-000000000008",
      asset_id: "abababab-abab-abab-abab-abababababab", // LTC
      network_id: "66666666-6666-6666-6666-666666666666", // LTC network
      contract_address: null,
      bitgo_coin: "ltc",
      deposits_enabled: true,
      withdrawals_enabled: true,
      min_deposit_atoms: "100000", // 0.001 LTC
      confirmations_override: null,
      metadata: JSON.stringify({
        provider: "BITGO",
        flat_withdrawal_fee_atoms: "10000",
        flat_withdrawal_fee: "0.0001"
      })
    },
    {
      id: "ffffffff-0009-0009-0009-000000000009",
      asset_id: "cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd", // DOGE
      network_id: "77777777-7777-7777-7777-777777777777", // DOGE network
      contract_address: null,
      bitgo_coin: "doge",
      deposits_enabled: true,
      withdrawals_enabled: true,
      min_deposit_atoms: "1000000000", // 10 DOGE
      confirmations_override: null,
      metadata: JSON.stringify({
        provider: "BITGO",
        flat_withdrawal_fee_atoms: "100000000",
        flat_withdrawal_fee: "1"
      })
    },
    {
      id: "ffffffff-000a-000a-000a-00000000000a",
      asset_id: "efefefef-efef-efef-efef-efefefefefef", // ZEC
      network_id: "88888888-8888-8888-8888-888888888888", // ZEC network
      contract_address: null,
      bitgo_coin: "zec",
      deposits_enabled: true,
      withdrawals_enabled: true,
      min_deposit_atoms: "100000", // 0.001 ZEC
      confirmations_override: null,
      metadata: JSON.stringify({
        provider: "BITGO",
        flat_withdrawal_fee_atoms: "10000",
        flat_withdrawal_fee: "0.0001"
      })
    },
    {
      id: "ffffffff-0005-0005-0005-000000000005",
      asset_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", // ETH
      network_id: "33333333-3333-3333-3333-333333333333", // ETH Sepolia
      contract_address: null,
      bitgo_coin: "teth",
      deposits_enabled: false,
      withdrawals_enabled: false,
      min_deposit_atoms: "1000000000000000", // 0.001 ETH
      confirmations_override: null,
      metadata: JSON.stringify({ testnet: true })
    }
  ];

  await knex("asset_networks")
    .insert(assetNetworks)
    .onConflict("id")
    .ignore();

  const withdrawalPolicies = [
    {
      asset_network_id: "ffffffff-0001-0001-0001-000000000001", // BTC
      min_withdrawal_atoms: "10000", // 0.0001 BTC
      required_approvals: 1,
      high_risk_approvals: 2,
      enabled: true,
      metadata: JSON.stringify({
        withdrawalFeeAtoms: "5000",
        withdrawalFee: "0.00005",
        flatFee: true,
        feeAsset: "BTC",
        rationale: "Lower flat BTC withdrawal fee and minimum for the current product policy."
      })
    },
    {
      asset_network_id: "ffffffff-0002-0002-0002-000000000002", // ETH
      min_withdrawal_atoms: "10000000000000000", // 0.01 ETH
      required_approvals: 1,
      high_risk_approvals: 2,
      enabled: false,
      metadata: JSON.stringify({
        withdrawalFeeAtoms: "3000000000000000",
        withdrawalFee: "0.003",
        flatFee: true,
        feeAsset: "ETH",
        rationale: "Flat fee includes gas headroom plus an operating margin."
      })
    },
    {
      asset_network_id: "ffffffff-0007-0007-0007-000000000007", // SOL
      min_withdrawal_atoms: "100000000", // 0.1 SOL
      required_approvals: 1,
      high_risk_approvals: 2,
      enabled: false,
      metadata: JSON.stringify({
        withdrawalFeeAtoms: "10000000",
        withdrawalFee: "0.01",
        flatFee: true,
        feeAsset: "SOL",
        rationale: "Flat fee covers Solana network fees, BitGo operations, and exchange margin."
      })
    },
    {
      asset_network_id: "ffffffff-0008-0008-0008-000000000008", // LTC
      min_withdrawal_atoms: "100000", // 0.001 LTC
      required_approvals: 1,
      high_risk_approvals: 2,
      enabled: true,
      metadata: JSON.stringify({
        withdrawalFeeAtoms: "10000",
        withdrawalFee: "0.0001",
        flatFee: true,
        feeAsset: "LTC",
        rationale: "Flat fee for Litecoin BitGo withdrawals."
      })
    },
    {
      asset_network_id: "ffffffff-0009-0009-0009-000000000009", // DOGE
      min_withdrawal_atoms: "1000000000", // 10 DOGE
      required_approvals: 1,
      high_risk_approvals: 2,
      enabled: true,
      metadata: JSON.stringify({
        withdrawalFeeAtoms: "100000000",
        withdrawalFee: "1",
        flatFee: true,
        feeAsset: "DOGE",
        rationale: "Flat fee for Dogecoin BitGo withdrawals."
      })
    },
    {
      asset_network_id: "ffffffff-000a-000a-000a-00000000000a", // ZEC
      min_withdrawal_atoms: "100000", // 0.001 ZEC
      required_approvals: 1,
      high_risk_approvals: 2,
      enabled: true,
      metadata: JSON.stringify({
        withdrawalFeeAtoms: "10000",
        withdrawalFee: "0.0001",
        flatFee: true,
        feeAsset: "ZEC",
        rationale: "Flat fee for Zcash BitGo withdrawals."
      })
    }
  ];

  const hasWithdrawalPolicies = await knex.schema.hasTable("withdrawal_policies");
  if (hasWithdrawalPolicies) {
    for (const policy of withdrawalPolicies) {
      await knex("withdrawal_policies")
        .insert(policy)
        .onConflict("asset_network_id")
        .merge({
          min_withdrawal_atoms: policy.min_withdrawal_atoms,
          required_approvals: policy.required_approvals,
          high_risk_approvals: policy.high_risk_approvals,
          enabled: policy.enabled,
          metadata: policy.metadata,
          updated_at: knex.fn.now()
        });
    }
  }
};
