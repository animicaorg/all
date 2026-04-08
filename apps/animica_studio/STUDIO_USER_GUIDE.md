# Animica Studio User Guide

## What Studio Is For

Animica Studio is the desktop app for the basic Animica path:

1. create or import a wallet
2. connect to a node or RPC
3. verify sync and balance
4. send and receive funds
5. optionally mine or use ENA, AICF, and DA features

The app is designed so you can stay inside a small number of main screens instead of jumping between disconnected tools.

## First Launch

On first launch, Studio opens a setup wizard.

### Step 1: Choose a network

- Pick `Mainnet`, `Testnet`, `Devnet`, or `Local Devnet`.
- Pick either `Managed local node` or `External RPC`.

Use `Managed local node` if you want Studio to start and monitor the node for you.

### Step 2: Set up a wallet

Choose one of:

- `Use existing`: keep the wallets already on this machine
- `Create a new wallet`: create a wallet now
- `Import`: import an existing `wallets.json`

For most users, `Dilithium3` is the right default when creating a wallet.

### Step 3: Configure the node or RPC

If you chose a managed local node:

- confirm the local RPC URL
- confirm the node start command
- confirm the data directory
- keep `Start the local node during verification` enabled unless you want to do it later

If you chose external RPC:

- enter the RPC URL
- confirm chain ID
- add an explorer URL if you have one

### Step 4: Verify setup

Studio checks:

- wallet available
- RPC reachable
- node running
- sync status

If sync is still in progress, that is usually okay. You can finish and keep using `Home` and `Node` to monitor progress.

### Step 5: Finish

After verification, Studio opens to `Home`.

You can rerun onboarding from `Settings -> Developer -> Rerun Onboarding`.

## Home

`Home` is the main control center.

It shows:

- current wallet summary
- balance summary
- node online/offline state
- sync state
- peers
- mining/ENA/AICF/DA status
- recent activity
- warnings and action-needed cards

Use `Home` first whenever something looks wrong.

## Wallet

Use `Wallet` for all core money flows.

### Create or import

- Use the top-row actions to refresh, import a wallet file, or create a wallet.

### Receive

- Select the wallet on the left.
- Open `Receive`.
- Copy the address.

### Send

- Select the source wallet.
- Open `Send`.
- Enter the recipient address and amount.
- Optionally save the recipient to Contacts.

Studio validates the address and amount before submitting.

### History

- Open `History` to see recent pending or refreshed transactions.

### Contacts

- Save commonly used recipient addresses so the send flow is faster and less error-prone.

## Node and Sync

`Node` is where you confirm whether the chain connection is actually usable.

It shows:

- node process state
- RPC reachability
- sync state and percentage
- peer count
- head height and hash
- network/chain information
- recent node logs

Useful actions:

- `Start Node`
- `Stop Node`
- `Restart Node`
- `Refresh Status`
- `Force Sync`
- `Bootstrap Peers`
- `Discover Snapshot`
- `Open Logs`

If sync is stalled:

- refresh status
- check whether peers are zero
- run `Bootstrap Peers`
- open logs if the node is still not progressing

## Mining

Use `Mining` only after wallet and node basics are working.

Recommended order:

1. confirm the payout address matches your selected wallet
2. confirm the node or RPC is healthy
3. start with default settings
4. watch recent miner output before assuming mining is active

## ENA

`ENA` is the consolidated workspace for ENA tasks.

Use it for:

- contribution
- checkpoint management
- training
- publish
- inference

If ENA remote features are unavailable, configure the provider, endpoint, and model in `Settings -> Advanced -> ENA`.

## AICF

Use `AICF` for:

- checking credits
- reviewing claimable vs pending status
- tracking jobs and related outputs

If AICF is failing, first confirm the current RPC is reachable on `Home` or `Node`.

## DA

Use `DA` for:

- contribution/storage status
- storage directory checks
- upload/contribution workflows

If DA is unhealthy:

- confirm the configured path in `Settings`
- confirm the filesystem is writable
- check `Logs` for the most recent error text

## Settings

`Settings` is split into three levels.

### Basic

- network preset
- managed local node vs external RPC
- theme
- common toggles

### Advanced

- RPC URL
- explorer URL
- chain ID
- node command and data directory
- CLI path override
- DA storage path
- ENA provider/endpoint/model

### Developer

- version and runtime metadata
- resolved CLI
- diagnostics summary
- open logs
- rerun onboarding

## Logs and Diagnostics

Use `Logs` when something does not behave as expected.

You can:

- filter recent logs
- inspect structured issues
- copy a diagnostics bundle
- export a diagnostics bundle to a file

For bug reports or support, include the diagnostics bundle.

## Common Problems

### No wallet shown

- Open `Wallet`.
- Import `wallets.json` or create a new wallet.

### RPC offline

- Open `Node`.
- If using a local node, click `Start Node`.
- If using external RPC, confirm the URL in `Settings`.

### Sync not progressing

- Check peer count on `Node`.
- Use `Bootstrap Peers`.
- Open `Logs` if peer count stays at zero.

### Balance unavailable

- Confirm the selected wallet is correct.
- Confirm the RPC or explorer settings in `Settings`.
- Refresh the Wallet page after the node/RPC is healthy.

### Need to restart setup

- Open `Settings -> Developer`.
- Click `Rerun Onboarding`.
