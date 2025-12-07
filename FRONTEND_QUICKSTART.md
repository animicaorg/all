# Frontend Quickstart — Animica Monorepo

This guide helps you run all frontend apps locally for development.

---

## Prerequisites

- **Node.js** ≥ 18.18.0 (LTS 20+ recommended)
- **pnpm** 9.0.0 (install via `npm install -g pnpm@9.0.0`)
- **Git** for cloning the repository
- **Python 3.11+** (for backend services if needed)

---

## Quick Setup

### 1. Clone & Install Dependencies

```bash
# Clone the repository
git clone https://github.com/animicaorg/all.git
cd all

# Install pnpm if not already installed
npm install -g pnpm@9.0.0

# Install all workspace dependencies
pnpm install
```

### 2. Set Up Environment Variables

Each frontend app has an `.env.example` file. Copy and configure them:

```bash
# Explorer Web
cp explorer-web/.env.example explorer-web/.env.local

# Studio Web
cp studio-web/.env.example studio-web/.env.local

# Miner Dashboard
cp apps/miner-dashboard/.env.example apps/miner-dashboard/.env.local

# Wallet Extension
cp wallet-extension/.env.example wallet-extension/.env

# Website
cp website/.env.example website/.env.local
```

### 3. Start Local Devnet (Optional)

For full functionality, you'll need a running Animica node:

```bash
# From repo root
./setup.sh  # Initializes devnet configuration
# Then follow instructions to start the node
```

**Default devnet endpoints:**
- RPC: `http://localhost:8545`
- WebSocket: `ws://localhost:8546`
- Chain ID: `1337`

---

## Running Each App

### Explorer Web — Block Explorer

**Purpose:** Browse blocks, transactions, addresses, and chain activity.

```bash
cd explorer-web
pnpm dev
# Opens at http://localhost:5173
```

**Environment (.env.local):**
```ini
VITE_RPC_URL=http://localhost:8545
VITE_CHAIN_ID=1337
VITE_SERVICES_URL=http://localhost:8787
```

**Build & Preview:**
```bash
pnpm build
pnpm preview
```

**Tests:**
```bash
pnpm test          # Unit tests
pnpm e2e           # E2E tests (requires running node)
```

---

### Studio Web — Developer Portal & Contract IDE

**Purpose:** Edit, compile, simulate, deploy, and verify Python-VM contracts.

```bash
cd studio-web
pnpm dev
# Opens at http://localhost:5173
```

**Environment (.env.local):**
```ini
VITE_RPC_URL=http://localhost:8545
VITE_CHAIN_ID=1337
VITE_SERVICES_URL=http://localhost:8787
```

**Features:**
- In-browser compilation via studio-wasm (Pyodide)
- Contract deployment with wallet connection
- Simulation and gas estimation
- Verification via studio-services
- AI/Quantum job panels

**Build & Preview:**
```bash
pnpm build
pnpm preview
```

**Tests:**
```bash
pnpm test          # Unit tests
pnpm e2e           # E2E tests
```

---

### Miner Dashboard — Mining Stats & Controls

**Purpose:** Monitor miner status, hashrate, shares, and blocks found.

```bash
cd apps/miner-dashboard
pnpm dev
# Opens at http://localhost:5173
```

**Environment (.env.local):**
```ini
VITE_STRATUM_API_URL=http://127.0.0.1:8550
```

**Features:**
- Connection status to stratum pool/node
- Hashrate and share metrics
- Blocks found and rewards
- Real-time updates

**Build & Preview:**
```bash
pnpm build
pnpm preview
```

---

### Wallet Extension — Browser Extension Wallet

**Purpose:** Post-quantum wallet for signing transactions and connecting to dapps.

```bash
cd wallet-extension
pnpm dev
# Extension loads from dist-chrome or dist-firefox directories
```

**Loading in Browser:**

**Chrome/Edge:**
1. Open `chrome://extensions`
2. Enable Developer mode
3. Click "Load unpacked"
4. Select `wallet-extension/dist-chrome`

**Firefox:**
1. Open `about:debugging#/runtime/this-firefox`
2. Click "Load Temporary Add-on"
3. Select `wallet-extension/dist-firefox/manifest.json`

**Environment (.env):**
```ini
VITE_RPC_URL=http://127.0.0.1:8545/rpc
VITE_WS_URL=ws://127.0.0.1:8546
VITE_CHAIN_ID=1337
VITE_NETWORK_PRESET=devnet
```

**Build for Distribution:**
```bash
pnpm build:chrome    # Chrome/Edge bundle
pnpm build:firefox   # Firefox bundle
```

**Tests:**
```bash
pnpm test            # Unit tests
pnpm e2e             # E2E tests with demo dapp
```

---

### Website — Marketing & Docs Site

**Purpose:** Landing pages, documentation, and community links.

```bash
cd website
pnpm dev
# Opens at http://localhost:4321
```

**Environment (.env.local):**
```ini
PUBLIC_STUDIO_URL=http://localhost:5173
PUBLIC_EXPLORER_URL=http://localhost:5173
PUBLIC_RPC_URL=http://localhost:8545
PUBLIC_CHAIN_ID=1337
```

**Build & Preview:**
```bash
pnpm build
pnpm preview
```

**Technology:** Astro + MDX for fast static site generation.

---

### Native Wallet (Flutter) — Mobile/Desktop App

**Purpose:** Native wallet app for iOS, Android, macOS, Windows, Linux.

```bash
cd wallet
flutter pub get
flutter run  # Select your target device
```

**Requirements:**
- Flutter SDK 3.x+
- Platform-specific tooling (Xcode, Android Studio, etc.)

**Configuration:** See `wallet/README.md` and `wallet/.env.example`

---

## Running Multiple Apps Simultaneously

To work on integration scenarios (e.g., studio connecting to explorer, wallet extension signing for studio), run apps in separate terminals:

```bash
# Terminal 1: Start devnet node
./start_devnet.sh

# Terminal 2: Studio Web
cd studio-web && pnpm dev

# Terminal 3: Explorer Web
cd explorer-web && pnpm dev

# Terminal 4: Miner Dashboard
cd apps/miner-dashboard && pnpm dev

# Terminal 5: Wallet Extension dev mode
cd wallet-extension && pnpm dev
```

**Port Summary:**
- **Node RPC:** 8545 (HTTP), 8546 (WS)
- **Studio Services:** 8787
- **Studio Web:** 5173
- **Explorer Web:** 5173 (use `--port 5174` if conflict)
- **Miner Dashboard:** 5173 (use `--port 5175` if conflict)
- **Website:** 4321

---

## Common Configuration Variables

All frontend apps use environment variables prefixed with `VITE_` (or `PUBLIC_` for Astro/website).

| Variable | Description | Default Devnet Value |
|----------|-------------|---------------------|
| `VITE_RPC_URL` | Node JSON-RPC endpoint | `http://localhost:8545` |
| `VITE_CHAIN_ID` | Network chain ID | `1337` |
| `VITE_WS_URL` | WebSocket endpoint | `ws://localhost:8546` |
| `VITE_SERVICES_URL` | studio-services base URL | `http://localhost:8787` |
| `VITE_STRATUM_API_URL` | Stratum/mining API | `http://127.0.0.1:8550` |

---

## Network Indicator & Configuration

All apps display the current network prominently:
- **Devnet 1337** (local development)
- **Testnet 2** (public testnet)
- **Mainnet 1** (production)

If the RPC/WS is unreachable, apps show:
- ⚠️ Warning banner with current endpoint
- Link to Network Settings
- Clear error message (not just console logs)

---

## Design System & Shared Components

Apps use a cohesive design system with:
- **CSS Variables:** Defined in `src/styles/tokens.css` and `src/styles/theme.css`
- **Tailwind (optional):** Configured in `tailwind.config.cjs` to consume CSS vars
- **Color Palette:** Light + dark mode support
- **Typography:** Inter font family, fluid scale
- **Components:** Button, Input, Card, Modal, Tabs, Toast, Badge, Skeleton

**Primitives location (per app):**
- `src/components/ui/*` — Reusable UI primitives
- `src/styles/*` — Design tokens and theme

---

## Error Handling & Loading States

All async actions show:
- **Loading:** Spinners, skeleton screens, disabled buttons
- **Success:** Toast/banner with confirmation
- **Error:** Human-readable message, optional "Details" for developers
- **Network Issues:** Prominent banner with retry option

---

## Testing

### Unit Tests
```bash
cd <app-name>
pnpm test
```

### E2E Tests
```bash
cd <app-name>
pnpm e2e
```

### Build Validation
```bash
cd <app-name>
pnpm build
```

---

## Troubleshooting

### CORS Errors
- Ensure RPC/Services allow your frontend origin
- Use dev proxy (see `scripts/dev_proxy.mjs` in studio-web/explorer-web)

### Chain ID Mismatch
- Verify `VITE_CHAIN_ID` matches your node's chain ID
- Check wallet extension is on same network

### Wallet Not Detected
- Ensure wallet-extension is loaded in browser
- Check `window.animica` provider is available in console

### Build Failures
- Clear `node_modules` and reinstall: `pnpm install`
- Clear build cache: `rm -rf dist .vite`
- Check Node.js version: `node -v` (should be ≥18.18.0)

### Port Conflicts
- Use `--port` flag: `vite --port 5174`
- Or set in `vite.config.ts`: `server: { port: 5174 }`

---

## Production Builds

### Static Hosting (Netlify, Vercel, GitHub Pages)

```bash
cd <app-name>
pnpm build
# Output in dist/ (or out/ for website)
```

**Deploy:**
- Set environment variables in hosting platform
- Point build output to `dist/` directory
- Configure SPA fallback for React apps
- Set cache headers:
  - `index.html`: `no-store`
  - `assets/*`: `max-age=31536000, immutable`

---

## Mobile Optimization & Responsive Design

All frontend apps have been optimized for mobile devices with a mobile-first approach.

### Supported Viewports

Test your changes at these common viewport widths:

- **Small phones**: 360px × 640px (e.g., iPhone SE, Galaxy S8)
- **Large phones**: 414px × 896px (e.g., iPhone 11/12/13)
- **Tablets**: 768px × 1024px (iPad, Android tablets)
- **Laptops**: 1024px × 768px and up
- **Desktops**: 1280px+ wide

### Mobile Features

#### Responsive Navigation
- **Miner Dashboard**: Floating action button (FAB) menu on mobile with slide-up navigation
- **Explorer Web**: Horizontal scrollable navigation on mobile, vertical sidebar on desktop
- **Studio Web**: Auto-collapsing sidebar on viewports < 960px
- **Website**: Responsive breakpoints with mobile-optimized spacing

#### Touch-Friendly Design
- Minimum touch target size: **44×44px** (WCAG 2.1 AAA compliant)
- Inputs use **16px font size** on mobile to prevent iOS zoom
- Adequate spacing between interactive elements

#### Responsive Tables
- **Mobile stacked cards**: Tables convert to card-based layouts on small screens (miner-dashboard)
- **Horizontal scroll**: Tables with min-width and smooth scrolling on tablets
- **Truncation helpers**: Hash/address truncation for narrow viewports

#### Safe Areas & Browser Chrome
- Support for `env(safe-area-inset-*)` for notched devices
- Dynamic viewport units (`dvh`) where supported
- Bottom padding to account for mobile browser toolbars

### Testing Mobile Layouts

#### Browser DevTools
```bash
# Chrome/Edge DevTools
1. Press F12
2. Click "Toggle device toolbar" (Ctrl+Shift+M)
3. Select device or set custom dimensions
4. Test at 360px, 768px, and 1024px widths
```

#### Real Device Testing
```bash
# Expose dev server to local network
cd <app-directory>
pnpm dev:host  # For miner-dashboard
# Or manually:
vite --host 0.0.0.0 --port 5173

# Access from mobile device
http://<your-ip>:5173
```

### Known Mobile Edge Cases

1. **Safari iOS**: Fixed headers may overlap with content when scrolling. Use `position: sticky` with `top: 0` instead of `position: fixed`.

2. **Virtual Keyboard**: On iOS/Android, the keyboard pushes content up. Forms have extra bottom padding to compensate.

3. **Horizontal Scroll**: Prevented by `overflow-x: hidden` on body, but tables/code blocks have intentional horizontal scroll with `-webkit-overflow-scrolling: touch`.

4. **Touch Gestures**: Swipe back/forward can interfere with carousels. Use `touch-action` CSS property if needed.

### Performance Optimizations

- **Code splitting**: Heavy components (Monaco editor, charts) loaded on demand
- **Lazy routes**: Routes loaded as separate chunks
- **Image optimization**: Responsive images with proper sizing attributes
- **CSS containment**: Layout containment on cards/widgets for better paint performance

---

## Contributing

When working on frontend:
1. Run `pnpm lint` before committing
2. Ensure `pnpm build` passes
3. Test in both light and dark mode
4. **Test responsive layout at 360px, 768px, and 1024px+ widths**
5. **Verify touch targets are at least 44×44px**
6. Check for console errors/warnings
7. Add tests for new features
8. **Ensure no horizontal scroll on mobile devices**

---

## Additional Resources

- **SDK Docs:** `sdk/docs/`
- **Studio Services:** `studio-services/README.md`
- **Node Setup:** `QUICKSTART.md` (repo root)
- **Governance:** `governance/GOVERNANCE.md`

---

## Support

- **Issues:** [GitHub Issues](https://github.com/animicaorg/all/issues)
- **Docs:** See `docs/` directory for detailed specs
- **Community:** Links in website/README.md

---

## Summary of Commands

```bash
# Install dependencies (from repo root)
pnpm install

# Run each app (from app directory)
pnpm dev            # Development server
pnpm build          # Production build
pnpm preview        # Preview built app
pnpm test           # Unit tests
pnpm e2e            # E2E tests (if available)
pnpm lint           # Lint code

# Wallet extension specific
pnpm build:chrome   # Chrome bundle
pnpm build:firefox  # Firefox bundle
```

Happy hacking! 🚀
