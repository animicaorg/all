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

  await knex("networks").insert([animicaNetwork]).onConflict("code").ignore();

  // Insert ANM asset
  const anmAsset = {
    id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
    symbol: "ANM",
    name: "Animica",
    decimals: 18, // Animica uses 18 decimals (atoms = wei-like)
    active: true,
    metadata: JSON.stringify({ native: true })
  };

  await knex("assets").insert([anmAsset]).onConflict("symbol").ignore();

  // Insert ANM on Animica network
  const animicaAssetNetwork = {
    id: "ffffffff-0006-0006-0006-000000000006",
    asset_id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee", // ANM
    network_id: "44444444-4444-4444-4444-444444444444", // ANIMICA network
    contract_address: null, // native asset
    bitgo_coin: null, // not using BitGo
    deposits_enabled: true,
    withdrawals_enabled: true,
    min_deposit_atoms: "1000000000000000", // 0.001 ANM
    confirmations_override: null, // use network default (20)
    metadata: JSON.stringify({ provider: "ANIMICA_NODE" })
  };

  await knex("asset_networks")
    .insert([animicaAssetNetwork])
    .onConflict("id")
    .ignore();
};
