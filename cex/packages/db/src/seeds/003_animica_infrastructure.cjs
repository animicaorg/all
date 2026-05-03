/**
 * Seed 003: Animica Infrastructure
 * 
 * Seeds initial data for Animica network and ANM asset
 */

exports.seed = async function seed(knex) {
  // Insert Animica network
  const animicaNetwork = {
    id: "44444444-4444-4444-4444-444444444444",
    code: "ANIMICA",
    name: "Animica Mainnet",
    type: "ACCOUNT", // account-based blockchain
    confirmations_required: 20,
    active: true,
    metadata: JSON.stringify({
      chain_id: 1337,
      rpc_url: "http://127.0.0.1:8545/rpc",
      explorer_url: "https://explorer.animica.org"
    })
  };

  await knex("networks").insert([animicaNetwork]).onConflict("code").merge({
    name: animicaNetwork.name,
    type: animicaNetwork.type,
    confirmations_required: animicaNetwork.confirmations_required,
    active: animicaNetwork.active,
    metadata: animicaNetwork.metadata
  });

  // Insert ANM asset
  const anmAsset = {
    id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
    symbol: "ANM",
    name: "Animica",
    decimals: 9, // Animica uses 9 decimals
    active: true,
    metadata: JSON.stringify({ native: true })
  };

  await knex("assets").insert([anmAsset]).onConflict("symbol").merge({
    name: anmAsset.name,
    decimals: anmAsset.decimals,
    active: anmAsset.active,
    metadata: anmAsset.metadata
  });

  // Insert ANM on Animica network
  const animicaAssetNetwork = {
    id: "ffffffff-0006-0006-0006-000000000006",
    asset_id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee", // ANM
    network_id: "44444444-4444-4444-4444-444444444444", // ANIMICA network
    contract_address: null, // native asset
    bitgo_coin: null, // not using BitGo
    deposits_enabled: true,
    withdrawals_enabled: true,
    min_deposit_atoms: "1000000", // 0.001 ANM
    confirmations_override: null, // use network default (20)
    metadata: JSON.stringify({
      provider: "ANIMICA_NODE",
      flat_withdrawal_fee_atoms: "1000000000",
      flat_withdrawal_fee: "1"
    })
  };

  await knex("asset_networks")
    .insert([animicaAssetNetwork])
    .onConflict("id")
    .merge({
      deposits_enabled: animicaAssetNetwork.deposits_enabled,
      withdrawals_enabled: animicaAssetNetwork.withdrawals_enabled,
      min_deposit_atoms: animicaAssetNetwork.min_deposit_atoms,
      metadata: animicaAssetNetwork.metadata
    });

  const hasWithdrawalPolicies = await knex.schema.hasTable("withdrawal_policies");
  if (hasWithdrawalPolicies) {
    await knex("withdrawal_policies")
      .insert({
        asset_network_id: "ffffffff-0006-0006-0006-000000000006",
        min_withdrawal_atoms: "10000000000", // 10 ANM
        required_approvals: 1,
        high_risk_approvals: 2,
        enabled: true,
        metadata: JSON.stringify({
          withdrawalFeeAtoms: "1000000000",
          withdrawalFee: "1",
          flatFee: true,
          feeAsset: "ANM",
          rationale: "Flat fee covers Animica node transaction cost and exchange operations."
        })
      })
      .onConflict("asset_network_id")
      .merge({
        min_withdrawal_atoms: "10000000000",
        required_approvals: 1,
        high_risk_approvals: 2,
        enabled: true,
        metadata: JSON.stringify({
          withdrawalFeeAtoms: "1000000000",
          withdrawalFee: "1",
          flatFee: true,
          feeAsset: "ANM",
          rationale: "Flat fee covers Animica node transaction cost and exchange operations."
        }),
        updated_at: knex.fn.now()
      });
  }
};
