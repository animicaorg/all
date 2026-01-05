# Animica Bridge Service

Off-chain bridge service connecting the Animica Compute Platform to the Animica blockchain.

## Features

- **Blockchain Integration**: Connect compute platform to Animica L1
- **Payment Processing**: Handle ANM token payments on-chain
- **Proof Anchoring**: Submit compute receipts to chain
- **Reward Distribution**: Pay GPU contributors in ANM
- **Event Monitoring**: Watch chain events for payments and jobs

## Architecture

```
Compute Platform ←→ Bridge Service ←→ Animica Blockchain
      ↓                   ↓                   ↓
   Services           RPC Client          Smart Contracts
   (API, Billing)     (Python SDK)        (Python VM)
```

## Components

### 1. Chain Monitor
- Watches for on-chain payment events
- Monitors compute job submissions
- Tracks contributor registrations

### 2. Payment Processor
- Validates ANM payment intents
- Confirms transactions on-chain
- Credits user accounts after confirmation

### 3. Receipt Submitter
- Creates proof-of-execution receipts
- Submits to chain for verification
- Handles reorg protection

### 4. Reward Distributor
- Calculates contributor payouts
- Submits payout transactions
- Tracks reward history

## Development

```bash
cd packages/animica-bridge
pip install -r requirements.txt
python -m animica_bridge.main
```

## Configuration

```
ANIMICA_RPC_URL=http://localhost:8545
ANIMICA_CHAIN_ID=1337
BRIDGE_PRIVATE_KEY=<hex-encoded-private-key>
DATABASE_URL=postgresql://...
REDIS_URL=redis://localhost:6379/0
POLLING_INTERVAL=12  # seconds
```
