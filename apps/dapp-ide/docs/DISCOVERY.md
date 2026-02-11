# Discovery Document — Animica Dapp IDE

## Overview
This document summarizes the authoritative code, schemas, and RPC methods discovered in the Animica monorepo for building the Dapp IDE.

## A) Animica VM Compile/Run Interfaces

### studio-wasm Package
Location: `/studio-wasm/src/api/`

**Compiler API** (`compiler.ts`):
- `compileSource(params)`: Compiles Python source + manifest → IR bytes + code hash
  - Input: `{ source: string, manifest: JSON, withBytes?: boolean }`
  - Output: `{ ir: Uint8Array, codeHash?: string, abi?: JSON, diagnostics?: string[] }`
- Uses Pyodide (Python in WebAssembly) for deterministic compilation

**Simulator API** (`simulator.ts`):
- `simulateCall()`: Execute contract call locally (no chain interaction)
- `simulateDeploy()`: Simulate contract deployment
- Returns: execution results, logs, events, storage diffs

### Python VM Package
Location: `/vm_py/`

- CLI tools in `/vm_py/cli/` for compile/deploy/call
- Gas table: `/vm_py/gas_table.json` defines deterministic gas costs
- Runtime in `/vm_py/runtime/` handles execution
- Standard library in `/vm_py/stdlib/` (abi, events, storage modules)

## B) Contract Artifact Formats

### Manifest Schema
Location: `/spec/manifest.schema.json`

**Required fields**:
- `manifestVersion`: semver string (e.g., "1.0.0")
- `encoding`: "animica-manifest/1"
- `package`: { name, version, description, authors, license }
- `target`: { vm: "python", vmVersion, abiVersion }
- `entrypoint`: primary module path
- `code`: { source, ir, toolchain }
- `abi`: ABI object or reference
- `capabilities`: { required: [], optional: [], resourceLimits: {} }
- `integrity`: { codeHash, abiHash, manifestHash, signatures }

**Code section**:
- Can contain `source` (Python files) and/or `ir` (precompiled)
- Source files have: path, sha3_256, size, mime
- IR blobs have: module, bytes (hex/base64), sha3_256, size

**Integrity hashes**:
- All hashes are sha3-256 (0x-prefixed 64 hex chars)
- Deterministic compilation must produce reproducible hashes

### ABI Schema
Location: `/spec/abi.schema.json`

**Structure**:
```json
{
  "abiVersion": "1.0.0",
  "encoding": "animica-abi/1",
  "contract": { "name": "...", "capabilities": [] },
  "functions": [
    {
      "name": "deploy",
      "kind": "deploy",
      "stateMutability": "nonpayable",
      "inputs": [...],
      "outputs": []
    },
    {
      "name": "methodName",
      "kind": "call",
      "stateMutability": "view|nonpayable|payable",
      "inputs": [...],
      "outputs": [...]
    }
  ],
  "events": [...],
  "errors": [...]
}
```

**Type system**:
- Scalars: bool, address, string, bytes, bytesN, u{8|16|32|64|128|256}, i{8|16|32|64|128|256}
- Complex: array, tuple, struct
- Function selectors: 4-byte sha3_256 hash of canonical signature

## C) RPC Methods for Contract Deployment and Calls

### SDK TypeScript
Location: `/sdk/typescript/src/`

**Deploy Transaction** (`contracts/deployer.ts`):
- `buildDeployTx(params)`: Build unsigned deploy transaction
  - Params: { chainId, from, code, manifest, value, nonce, gasPrice, gasLimit }
  - Returns: UnsignedTx
- `deploy(client, signer, params)`: High-level deploy (build → sign → submit → wait)
  - Returns: { txHash, receipt, contractAddress, codeHashHex, manifestHashHex }

**Contract Interaction** (`contracts/client.ts`):
- View calls: RPC method for read-only contract calls (no tx)
- Write calls: Build transaction, sign with wallet, submit via RPC

**RPC Transport** (`rpc/http.ts`):
- `createHttpClient(baseUrl, opts)`: JSON-RPC 2.0 client
- Methods:
  - `request<R>(method, params, opts)`: Generic RPC call
  - Supports retries, timeouts, batch requests
  - Returns typed responses

**Transaction Sending** (`tx/send.ts`):
- `signSendAndWait(client, tx, signer, waitOpts)`: Complete flow
  - Signs transaction with PQ signer (Dilithium3/SPHINCS+)
  - Submits to RPC
  - Polls for receipt
  - Returns: { txHash, receipt }

### RPC Method Names (inferred from SDK)
- Chain queries: `chain.getHead`, `chain.getBlock`, `chain.getHeight`
- Transaction: `tx.send`, `tx.getReceipt`, `tx.estimateGas`
- Contract: likely `contract.call`, `contract.simulate` (view calls)
- State: `state.get`, `state.getStorage` (if exposed)

## D) Existing Dapp Templates

### dapp-react-ts Template
Location: `/templates/dapp-react-ts/`

**Structure**:
- React + TypeScript + Vite
- Provider integration in `/services/provider.ts`
- SDK client in `/services/sdk.ts`
- Example components for wallet connection

**Key patterns**:
- Detect `window.animica` on mount
- Call `animica_requestAccounts()` to connect
- Listen for `accountsChanged`, `chainChanged` events
- Use SDK RPC client for chain interactions

### contract-python-basic Template
Location: `/templates/contract-python-basic/`

- Minimal Python contract template
- Includes manifest.json structure
- Example deployment flow

## E) Explorer Patterns

### explorer-web
Location: `/explorer-web/`

**Services** (`src/services/explorerApi.ts`):
- Fetches blocks, transactions, accounts
- Displays receipts, logs, events
- Shows contract state (if API exposes it)

**Patterns for state/logs viewer**:
- Receipt includes: status, gasUsed, logs[], contractAddress
- Events have: topics[], data
- Can decode using ABI

## Compile Pipeline (Canonical)

1. **Input**: Python source code + manifest JSON
2. **Compile**: `studio-wasm` → `compileSource({ source, manifest })`
3. **Output**: 
   - `ir`: Uint8Array (compiled bytecode)
   - `codeHash`: 0x... (sha3-256 of IR)
   - `abi`: ABI JSON (if not in manifest)
4. **Package**: Combine IR + manifest + ABI into deploy bundle
5. **Deploy**: Build deploy tx with `buildDeployTx({ code: ir, manifest: manifestBytes })`
6. **Sign**: Use wallet extension to sign transaction
7. **Submit**: Send via RPC `tx.send`
8. **Track**: Poll `tx.getReceipt` until mined

## Artifact Formats Summary

**Compiled IR**: Uint8Array (opaque bytecode for vm_py runtime)
**Manifest**: JSON conforming to `/spec/manifest.schema.json`
**ABI**: JSON conforming to `/spec/abi.schema.json`
**Deploy bundle**: Typically manifest bytes (which embeds code hash) + IR blob

## Wallet Extension Provider (window.animica)

Location: `/apps/wallet-extension/src/provider/index.ts`

**Interface**:
```typescript
interface AnimicaProvider {
  isAnimica: boolean;
  request(args: { method: string; params?: any[] }): Promise<any>;
  
  // Convenience methods
  animica_requestAccounts(): Promise<string[]>;
  animica_accounts(): Promise<string[]>;
  animica_chainId(): Promise<number>;
  animica_switchChain(chainId: number): Promise<void>;
  animica_signMessage(message: string): Promise<string>;
  animica_sendTransaction(tx: any): Promise<string>;
  
  // Event handling
  on(event: string, handler: (...args: any[]) => void): void;
  removeListener(event: string, handler: (...args: any[]) => void): void;
}
```

**Internal methods** (called via `request`):
- `provider_requestAccounts`: Request account access
- `provider_getAccounts`: Get current accounts
- `provider_getChainId`: Get current chain ID
- `wallet_switchNetwork`: Switch network
- `provider_signMessage`: Sign arbitrary message
- `provider_sendTransaction`: Sign and broadcast transaction

**Events**:
- `accountsChanged`: Fired when active account changes
- `chainChanged`: Fired when network switches
- `disconnect`: Fired when wallet disconnects

**Communication**: Uses `window.postMessage` for content script ↔ extension communication

## Network Configuration

**Known networks** (from codebase):
- **Local**: `http://127.0.0.1:8545/rpc` (default for development)
- **Devnet**: To be discovered from repo (check `/tests/devnet/` or config files)
- **Mainnet**: `http://144.126.133.21:8545/rpc` (specified in requirements)

**Chain IDs**:
- Local/dev: 1337
- Testnet: TBD
- Mainnet: TBD (query via RPC or hardcode)

## Contract Templates to Include in IDE

1. **Hello Contract**: Minimal example with single storage value
2. **Storage Counter**: `/contracts/templates/counter/` — inc/get operations
3. **Token-like**: Basic balance tracking (if exists in `/contracts/examples/token/`)
4. **Events Demo**: Contract that emits various event types

## Implementation Notes

### Determinism Requirements
- All compilation must use `studio-wasm` (Pyodide-based) for reproducibility
- No server-side compilation unless explicitly fallback
- Manifest and ABI hashes must match canonical encoding

### Security Considerations
- Never store private keys in IDE
- All signing via `window.animica` (wallet extension)
- Validate user inputs before compilation
- Warn users when connecting to mainnet

### Storage Strategy
- Use IndexedDB for project persistence (files, manifest, compiled artifacts)
- Store: project metadata, file tree, source code, compiled IR, ABI
- Enable export/import of projects as ZIP

### Editor Features
- Monaco editor with Python syntax highlighting
- Basic linting (parse errors)
- Problems panel for compiler diagnostics
- Multi-file support with tabs

### Build Flow
1. User clicks "Build"
2. Collect all project files
3. Call `studio-wasm.compileSource()`
4. Display compiler output (diagnostics, gas estimates)
5. Store compiled IR + ABI in project
6. Show artifact viewer (code hash, size, ABI summary)

### Deploy Flow
1. User selects compiled artifact
2. Show deploy form (constructor args from ABI)
3. Estimate gas (optional)
4. Click "Deploy"
5. Request `window.animica.animica_sendTransaction(deployTx)`
6. Wallet shows confirmation modal
7. User approves
8. Track tx hash, poll receipt
9. Display contract address when mined

### Interact Flow
1. User enters contract address
2. Load ABI (from project or upload)
3. Generate UI from ABI functions
4. View functions: direct RPC call (no tx)
5. Write functions: build tx → wallet sign → submit
6. Display results, events, state changes

## Next Steps
1. Scaffold `apps/dapp-ide/` with Vite + React + TypeScript
2. Install dependencies: `monaco-editor`, `zustand`, `idb`, `@animica/sdk`
3. Integrate `studio-wasm` for compilation
4. Build wallet adapter using discovered provider interface
5. Implement project storage with IndexedDB
6. Create file tree, editor, build panel, deploy panel, interact panel
7. Add contract templates matching discovered schemas
8. Test end-to-end against local node
