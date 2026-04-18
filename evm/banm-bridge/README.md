# BANM EVM Contracts

Hardhat workspace for BANM mint/burn bridge contracts.

## Install

```bash
cd evm/banm-bridge
pnpm install
```

## Build & Test

```bash
pnpm run build
pnpm run test
```

## Deploy

```bash
cp .env.example .env
pnpm run deploy --network bscTestnet
pnpm run grant-roles --network bscTestnet
pnpm run set-caps --network bscTestnet
pnpm run configure-chain --network bscTestnet
```

