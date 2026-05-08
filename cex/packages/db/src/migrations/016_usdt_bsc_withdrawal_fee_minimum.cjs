const USDT_ASSET_NETWORK_ID = "ffffffff-0003-0003-0003-000000000003";

const USDT_WITHDRAWAL_FEE_ATOMS = "1000000000000000000"; // 1 USDT
const USDT_MIN_WITHDRAWAL_ATOMS = "5000000000000000000"; // 5 USDT

const PREVIOUS_USDT_WITHDRAWAL_FEE_ATOMS = "10000000000000000000"; // 10 USDT
const PREVIOUS_USDT_MIN_WITHDRAWAL_ATOMS = "20000000000000000000"; // 20 USDT

exports.up = async function up(knex) {
  await knex("asset_networks")
    .where({ id: USDT_ASSET_NETWORK_ID })
    .update({
      metadata: knex.raw(
        `COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
          'flat_withdrawal_fee_atoms', ?::text,
          'flat_withdrawal_fee', ?::text
        )`,
        [USDT_WITHDRAWAL_FEE_ATOMS, "1"]
      ),
    });

  const hasWithdrawalPolicies = await knex.schema.hasTable("withdrawal_policies");
  if (hasWithdrawalPolicies) {
    await knex("withdrawal_policies")
      .where({ asset_network_id: USDT_ASSET_NETWORK_ID })
      .update({
        min_withdrawal_atoms: USDT_MIN_WITHDRAWAL_ATOMS,
        metadata: knex.raw(
          `COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
            'withdrawalFeeAtoms', ?::text,
            'withdrawalFee', ?::text,
            'flatFee', true,
            'feeAsset', 'USDT',
            'rationale', 'Flat fee for BNB Smart Chain BEP-20 USDT withdrawals through BitGo.'
          )`,
          [USDT_WITHDRAWAL_FEE_ATOMS, "1"]
        ),
        updated_at: knex.fn.now(),
      });
  }
};

exports.down = async function down(knex) {
  await knex("asset_networks")
    .where({ id: USDT_ASSET_NETWORK_ID })
    .update({
      metadata: knex.raw(
        `COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
          'flat_withdrawal_fee_atoms', ?::text,
          'flat_withdrawal_fee', ?::text
        )`,
        [PREVIOUS_USDT_WITHDRAWAL_FEE_ATOMS, "10"]
      ),
    });

  const hasWithdrawalPolicies = await knex.schema.hasTable("withdrawal_policies");
  if (hasWithdrawalPolicies) {
    await knex("withdrawal_policies")
      .where({ asset_network_id: USDT_ASSET_NETWORK_ID })
      .update({
        min_withdrawal_atoms: PREVIOUS_USDT_MIN_WITHDRAWAL_ATOMS,
        metadata: knex.raw(
          `COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
            'withdrawalFeeAtoms', ?::text,
            'withdrawalFee', ?::text,
            'flatFee', true,
            'feeAsset', 'USDT',
            'rationale', 'Flat fee for BNB Smart Chain BEP-20 USDT withdrawals through BitGo.'
          )`,
          [PREVIOUS_USDT_WITHDRAWAL_FEE_ATOMS, "10"]
        ),
        updated_at: knex.fn.now(),
      });
  }
};
