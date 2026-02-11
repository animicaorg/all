# Animica Dapp IDE

A complete browser-based IDE for building, compiling, deploying, and interacting with Animica smart contracts.

## Features

- **Monaco Editor**: Full-featured code editor with Python syntax highlighting, IntelliSense, and error detection
- **In-Browser Compilation**: Compile Python contracts to IR using `studio-wasm` (Pyodide-based)
- **Contract Deployment**: Deploy compiled contracts to Animica networks via wallet integration
- **Contract Interaction**: Generate UI from ABI to call contract functions
- **Project Management**: Persistent project storage using IndexedDB
- **Wallet Integration**: Seamless integration with Animica wallet extension (`window.animica`)
- **Contract Templates**: Pre-built templates (Hello World, Counter, etc.) to get started quickly
- **Multi-Network Support**: Configure and switch between Local, Testnet, and Mainnet

## Architecture

```
apps/dapp-ide/
├── src/
│   ├── pages/           # Route pages (Home, IDE, Deploy, Interact, Settings)
│   ├── components/      # React components (FileTree, Editor, BuildPanel, etc.)
│   ├── animica/         # Animica-specific integrations
│   │   ├── wallet/      # Wallet provider adapter (window.animica)
│   │   ├── rpc/         # JSON-RPC client
│   │   ├── vm/          # Compiler integration (studio-wasm)
│   │   ├── contracts/   # Deployment logic
│   │   └── types/       # TypeScript types
│   ├── project/         # Project management
│   │   ├── storage/     # IndexedDB persistence
│   │   ├── templates/   # Contract templates
│   │   └── bundler/     # Build artifact bundling
│   └── editor/          # Editor-specific logic
│       ├── monaco/      # Monaco editor configuration
│       └── fileTree/    # File tree management
├── public/              # Static assets
└── docs/                # Documentation
```

## Development Setup

### Prerequisites

- Node.js >= 18.0.0
- pnpm (package manager)
- Animica wallet extension (for deployment and interaction)

### Installation

```bash
# Install dependencies (from monorepo root)
pnpm install

# Start development server
cd apps/dapp-ide
pnpm dev
```

The IDE will be available at `http://localhost:5174`.

### Build

```bash
# Type-check and build
pnpm build:check

# Build only
pnpm build

# Preview production build
pnpm preview
```

## Usage

### 1. Create a New Project

- Click "Open IDE" from the home page
- Start with a blank project or choose a template (Hello World, Counter, etc.)
- Edit files in the Monaco editor

### 2. Build Contract

- Click "Build" in the bottom panel
- The IDE compiles your Python code using `studio-wasm`
- View compilation output, diagnostics, and generated ABI

### 3. Deploy Contract

- Navigate to "Deploy Contract" page
- Ensure wallet is connected
- Enter constructor arguments (if any)
- Click "Deploy" and approve in wallet
- View transaction hash and deployed contract address

### 4. Interact with Contract

- Navigate to "Interact with Contract" page
- Enter deployed contract address
- Load ABI (from project or upload)
- Call view functions (read-only, no transaction)
- Execute write functions (requires wallet signature)

## Key Technologies

- **React 18** + **TypeScript**: Modern React with full type safety
- **Vite**: Fast build tool with HMR
- **Monaco Editor**: VS Code's editor in the browser
- **studio-wasm**: Python-VM compiler in WebAssembly (Pyodide)
- **@animica/sdk**: Animica SDK for RPC and contract interaction
- **IndexedDB** (via `idb`): Client-side project persistence
- **Zustand**: Lightweight state management
- **React Router**: Client-side routing

## Network Configuration

Default networks:

- **Local**: `http://127.0.0.1:8545/rpc` (Chain ID: 1337)
- **Mainnet**: `http://144.126.133.21:8545/rpc` (Chain ID: 1)

Configure custom networks in Settings page.

## Wallet Integration

The IDE integrates with the Animica wallet extension via `window.animica`:

- **Connect**: `animica_requestAccounts()`
- **Sign Transactions**: `animica_sendTransaction(tx)`
- **Sign Messages**: `animica_signMessage(message)`
- **Events**: Listen for `accountsChanged`, `chainChanged`, `disconnect`

## Contract Templates

### Hello World
Basic contract with a single storage value (greeting message).

### Counter
Simple counter with increment/decrement operations.

More templates coming soon!

## Development

### Project Structure

- **Pages**: Top-level route components
- **Components**: Reusable UI components
- **Animica modules**: Core integrations (wallet, RPC, compiler, contracts)
- **Project modules**: Project management and storage
- **Editor modules**: Monaco editor and file tree logic

### Adding a New Template

1. Edit `src/project/templates/index.ts`
2. Add your template with Python source, manifest, and ABI
3. Template will appear in project creation flow

### Extending the IDE

- **Add language support**: Configure Monaco for new languages
- **Custom build steps**: Extend `src/animica/vm/compiler.ts`
- **New RPC methods**: Add to `src/animica/rpc/client.ts`

## Testing

```bash
# Run unit tests
pnpm test

# Run tests with UI
pnpm test:ui

# Run E2E tests
pnpm e2e
```

## Deployment

Build and deploy to static hosting:

```bash
pnpm build
# Deploy the `dist/` directory
```

Compatible with Netlify, Vercel, GitHub Pages, etc.

## Contributing

See [CONTRIBUTING.md](../../CONTRIBUTING.md) for contribution guidelines.

## License

See [LICENSE.txt](../../LICENSE.txt) for license information.

## Links

- [Animica Documentation](../../docs/)
- [Python VM Specs](../../vm_py/specs/)
- [Contract Examples](../../contracts/)
- [SDK Documentation](../../sdk/docs/)
