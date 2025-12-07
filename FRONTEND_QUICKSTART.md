# Animica Frontend Quickstart

This guide provides everything you need to run all Animica front-end applications locally and connect them to a devnet or testnet.

## Prerequisites

- **Node.js** ≥ 18 (LTS 20 recommended)
- **pnpm** ≥ 9.0.0 (package manager)
- **Git**
- Running **Animica node** with RPC/WebSocket enabled (for most apps)

## Quick Setup

### 1. Clone and Install

```bash
git clone https://github.com/animicaorg/all.git
cd all

# Install pnpm if not already installed
npm install -g pnpm@9.0.0

# Install all workspace dependencies
pnpm install --no-frozen-lockfile
```

### 2. Configure Environment Variables

Each app has an `.env.example` file. Copy it to `.env.local` or `.env` and configure:

#### Common Configuration

```bash
# RPC endpoint (adjust for your node)
VITE_RPC_URL=http://localhost:8545
VITE_RPC_WS=ws://localhost:8546

# Chain ID (must match your node)
VITE_CHAIN_ID=1

# Optional: Studio services (for deploy/verify)
VITE_SERVICES_URL=http://localhost:8080
```

### 3. Run Applications

#### Miner Dashboard

Monitor mining operations, hashrate, shares, and rewards.

```bash
# From workspace root
pnpm --filter miner-dashboard dev

# Or from the app directory
cd apps/miner-dashboard
pnpm dev
```

**Access**: http://localhost:5173 (default Vite port)

**Configuration** (`apps/miner-dashboard/.env.local`):
```env
VITE_POOL_URL=http://localhost:8332
VITE_POOL_WS=ws://localhost:8333
```

#### Wallet Extension

Browser extension wallet with post-quantum signatures.

```bash
cd wallet-extension

# Development mode (with live reload)
pnpm dev

# Production builds
pnpm build:chrome    # For Chrome/Edge/Brave
pnpm build:firefox   # For Firefox
```

**Load Extension**:
- **Chrome**: Visit `chrome://extensions`, enable Developer mode, click "Load unpacked", select `wallet-extension/dist-chrome`
- **Firefox**: Visit `about:debugging#/runtime/this-firefox`, click "Load Temporary Add-on", select `wallet-extension/dist-firefox/manifest.json`

**Configuration** (`wallet-extension/.env`):
```env
RPC_URL=http://localhost:8545
CHAIN_ID=1
```

#### Explorer Web

Blockchain explorer for blocks, transactions, and addresses.

```bash
# Note: Explorer currently has TypeScript compilation issues
# These need to be resolved before running

cd explorer-web
pnpm dev
```

**Access**: http://localhost:5174

**Configuration** (`explorer-web/.env.local`):
```env
VITE_RPC_URL=http://localhost:8545
VITE_RPC_WS=ws://localhost:8546
VITE_CHAIN_ID=1
```

#### Studio Web

Web IDE for contract development, simulation, and deployment.

```bash
cd studio-web
pnpm dev
```

**Access**: http://localhost:5175

**Configuration** (`studio-web/.env.local`):
```env
VITE_RPC_URL=http://localhost:8545
VITE_CHAIN_ID=1
VITE_SERVICES_URL=http://localhost:8080
```

#### Website & Docs

Marketing site and documentation hub.

```bash
cd website
pnpm dev
```

**Access**: http://localhost:4321 (Astro default)

**Configuration** (`website/.env.local`):
```env
PUBLIC_RPC_URL=https://rpc.animica.org
PUBLIC_CHAIN_ID=1
PUBLIC_EXPLORER_URL=https://explorer.animica.org
PUBLIC_STUDIO_URL=https://studio.animica.org
```

## Development Workflows

### Run All UIs Concurrently

From the workspace root:

```bash
# This uses pnpm workspaces to run all dev servers
pnpm --filter miner-dashboard dev &
pnpm --filter wallet-extension dev &
pnpm --filter studio-web dev &
pnpm --filter animica-website dev &
```

### Build All Apps

```bash
# Build all workspace apps
pnpm --filter "miner-dashboard" build
pnpm --filter "studio-web" build
pnpm --filter "animica-website" build
pnpm --filter "@animica/wallet-extension" build:chrome
pnpm --filter "@animica/wallet-extension" build:firefox
```

### Run Tests

```bash
# Run tests for specific app
pnpm --filter studio-web test
pnpm --filter wallet-extension test

# Run E2E tests
pnpm --filter studio-web e2e
pnpm --filter wallet-extension e2e
```

### Lint & Format

```bash
# Lint specific app
pnpm --filter studio-web lint

# Format (where configured)
pnpm --filter wallet-extension format
```

## Port Reference

Default development ports:

| Application | Port | Protocol |
|-------------|------|----------|
| Miner Dashboard | 5173 | HTTP |
| Explorer Web | 5174 | HTTP |
| Studio Web | 5175 | HTTP |
| Website | 4321 | HTTP |
| Node RPC | 8545 | HTTP |
| Node WebSocket | 8546 | WS |
| Studio Services | 8080 | HTTP |
| Pool Server | 8332 | HTTP |
| Pool WebSocket | 8333 | WS |

## Network Configuration

### Devnet (Local Development)

```env
VITE_CHAIN_ID=1
VITE_RPC_URL=http://localhost:8545
VITE_RPC_WS=ws://localhost:8546
```

### Testnet

```env
VITE_CHAIN_ID=2
VITE_RPC_URL=https://testnet-rpc.animica.org
VITE_RPC_WS=wss://testnet-rpc.animica.org
```

### Mainnet

```env
VITE_CHAIN_ID=1337
VITE_RPC_URL=https://rpc.animica.org
VITE_RPC_WS=wss://rpc.animica.org
```

## Troubleshooting

### CORS Errors

If you see CORS errors when connecting to RPC:

1. **Development**: Use a proxy (most apps include one)
2. **Node configuration**: Enable CORS on your Animica node:
   ```
   --rpc-cors "*"  # or specific origins
   ```

### Network Not Reachable

- Verify RPC URL is correct
- Check node is running: `curl -X POST http://localhost:8545 -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"chain_getHead","params":[],"id":1}'`
- Ensure firewall allows connections
- Check WebSocket connection if live updates aren't working

### Wallet Extension Not Detected

- Ensure extension is loaded in browser
- Check extension is enabled
- Reload the page after installing extension
- Check browser console for `window.animica` object

### Build Failures

**Explorer Web TypeScript Errors**:
```bash
# Current known issue with strict type checking
# Solution: The tsconfig has been modified to relax some checks
# Full fix requires addressing type issues in source files
```

**Wallet Extension Missing Exports**:
```bash
# Known non-blocking warnings about missing exports
# The build completes successfully despite warnings
```

### Port Already in Use

If a port is already in use, you can override it:

```bash
# Vite apps
pnpm dev --port 3000

# Astro (website)
pnpm dev --port 3001
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         User's Browser                          │
├─────────────────────────────────────────────────────────────────┤
│  Wallet Extension                                               │
│  (window.animica provider)                                      │
│         │                                                        │
│         ├──> Studio Web (Port 5175)                             │
│         │    - Contract IDE                                     │
│         │    - Compile/Simulate                                 │
│         │    - Deploy/Verify                                    │
│         │                                                        │
│         ├──> Explorer Web (Port 5174)                           │
│         │    - Block browser                                    │
│         │    - Transaction viewer                               │
│         │    - Address lookup                                   │
│         │                                                        │
│         └──> Miner Dashboard (Port 5173)                        │
│              - Mining stats                                     │
│              - Pool connection                                  │
│              - Rewards tracking                                 │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Backend Services                              │
├─────────────────────────────────────────────────────────────────┤
│  Animica Node (Port 8545/8546)                                  │
│  - JSON-RPC HTTP endpoint                                       │
│  - WebSocket subscriptions                                      │
│  - Block production                                             │
│                                                                  │
│  Studio Services (Port 8080)                                    │
│  - Contract deployment                                          │
│  - Source verification                                          │
│  - Faucet (testnet)                                             │
│                                                                  │
│  Pool Server (Port 8332/8333)                                   │
│  - Stratum protocol                                             │
│  - Share distribution                                           │
│  - Worker stats                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Design System

All apps are being updated to use a cohesive design system with:

### Color Palette

**Dark Theme** (default):
- Background: `#0B0C10`
- Panel: `#111318`
- Text: `#E8EDF2`
- Muted: `#A3ADBA`
- Accent: `#40A9FF`
- Success: `#3CCF91`
- Warning: `#F0A500`
- Error: `#FF6B6B`

**Light Theme**:
- Background: `#FFFFFF`
- Panel: `#F6F8FB`
- Text: `#0B0C10`
- Muted: `#4B5563`
- Accent: `#2563EB`
- Success: `#059669`
- Warning: `#B45309`
- Error: `#DC2626`

### Typography

- **Font Family**: Inter (variable font)
- **Base Size**: 14px
- **Line Height**: 1.5
- **Weights**: 400 (regular), 600 (semibold), 700 (bold)

### Spacing Scale

- `xs`: 0.25rem (4px)
- `sm`: 0.5rem (8px)
- `md`: 1rem (16px)
- `lg`: 1.5rem (24px)
- `xl`: 2rem (32px)
- `2xl`: 3rem (48px)

### Border Radius

- `sm`: 0.25rem
- `md`: 0.5rem
- `lg`: 1rem
- `full`: 9999px

## Next Steps

1. **Set up your node**: Follow the main README to run an Animica devnet node
2. **Configure environments**: Copy `.env.example` files and adjust for your setup
3. **Start developing**: Pick an app and run its dev server
4. **Deploy contracts**: Use Studio Web to write and deploy Python-VM contracts
5. **Monitor activity**: Use Explorer Web to watch blocks and transactions
6. **Mine blocks**: Connect to the pool via Miner Dashboard

## Additional Resources

- **Main README**: `/README.md`
- **SDK Documentation**: `/sdk/docs/`
- **Contract Templates**: `/contracts/packages/`
- **Architecture Docs**: `/docs/architecture/`
- **API Reference**: Available at your node's `/docs` endpoint when running

## Support

- **GitHub Issues**: https://github.com/animicaorg/all/issues
- **Documentation**: https://docs.animica.org
- **Discord**: [Link TBD]

## Contributing

See `CONTRIBUTING.md` for guidelines on:
- Code style
- Testing requirements
- PR process
- Design system usage

---

**Last Updated**: December 2025
**Version**: 0.1.0
