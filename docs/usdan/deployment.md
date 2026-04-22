# Deployment

## Contracts

```bash
./contracts/usdan/scripts/build_all.sh

python -m omni_sdk.cli.deploy --rpc "$ANIMICA_RPC_URL" --chain-id "$ANIMICA_CHAIN_ID" --keystore "$ANIMICA_KEYSTORE" --manifest contracts/packages/usdan_token/manifest.json --ir contracts/build/usdan_token/usdan_token.ir
python -m omni_sdk.cli.deploy --rpc "$ANIMICA_RPC_URL" --chain-id "$ANIMICA_CHAIN_ID" --keystore "$ANIMICA_KEYSTORE" --manifest contracts/packages/usdan_mint_controller/manifest.json --ir contracts/build/usdan_mint_controller/usdan_mint_controller.ir
python -m omni_sdk.cli.deploy --rpc "$ANIMICA_RPC_URL" --chain-id "$ANIMICA_CHAIN_ID" --keystore "$ANIMICA_KEYSTORE" --manifest contracts/packages/usdan_redemption_controller/manifest.json --ir contracts/build/usdan_redemption_controller/usdan_redemption_controller.ir
python -m omni_sdk.cli.deploy --rpc "$ANIMICA_RPC_URL" --chain-id "$ANIMICA_CHAIN_ID" --keystore "$ANIMICA_KEYSTORE" --manifest contracts/packages/usdan_compliance_controller/manifest.json --ir contracts/build/usdan_compliance_controller/usdan_compliance_controller.ir
python -m omni_sdk.cli.deploy --rpc "$ANIMICA_RPC_URL" --chain-id "$ANIMICA_CHAIN_ID" --keystore "$ANIMICA_KEYSTORE" --manifest contracts/packages/usdan_reserve_attestation/manifest.json --ir contracts/build/usdan_reserve_attestation/usdan_reserve_attestation.ir
```

After deploy, wire controller addresses into token.

## Backend

```bash
pnpm --filter @animica/usdan-api db:generate
pnpm --filter @animica/usdan-api db:migrate:deploy
pnpm --filter @animica/usdan-api build
pnpm --filter @animica/usdan-api start
```

## Frontend

```bash
pnpm --filter @animica/usdan-web build
pnpm --filter @animica/usdan-web preview
```
