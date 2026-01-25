const { v4: uuidv4 } = require("uuid");

exports.seed = async function seed(knex) {
  await knex("balances").del();
  await knex("journal_entries").del();
  await knex("orders").del();
  await knex("markets").del();
  await knex("users").del();

  const userId = uuidv4();

  await knex("users").insert({
    id: userId,
    email: "test@cex.local"
  });

  await knex("markets").insert({
    id: uuidv4(),
    symbol: "ANM/USDT",
    base_asset: "ANM",
    quote_asset: "USDT"
  });

  await knex("balances").insert({
    id: uuidv4(),
    account_id: userId,
    asset: "ANM",
    available: 1000,
    locked: 0
  });
};
