# Animica Dapp IDE - User Guide

## Overview

The Animica Dapp IDE is a web-based integrated development environment for building, compiling, deploying, and interacting with Animica Python smart contracts. It provides a complete IDE experience with Monaco editor, file management, build system, and seamless integration with the Animica Browser Wallet.

## Getting Started

### Prerequisites

1. **Animica Wallet Extension**: Install the Animica browser wallet extension
2. **Network Access**: Ensure you can connect to an Animica node (local, devnet, or mainnet)

### Launching the IDE

1. Navigate to the IDE URL (e.g., `http://localhost:5174` for development)
2. You'll see the main IDE interface with several panels:
   - **File Tree** (left sidebar): File management
   - **Editor** (center): Code editing with Monaco
   - **Build Panel** (bottom): Compilation output
   - **Wallet Status** (top right): Connection status

## Creating Your First Contract

### Step 1: Create a New File

1. Click the **"+ New"** button in the File Tree panel
2. Enter a filename (e.g., `contract.py`)
3. Select file type: **Python (.py)**
4. Click **Create**

The IDE will automatically populate the file with a contract template.

### Step 2: Edit Your Contract

The editor provides:
- **Syntax highlighting** for Python
- **Auto-save**: Changes are saved automatically to browser storage
- **Line numbers**
- **Code folding**
- **Search and replace** (Ctrl/Cmd+F)

#### Example: Simple Counter Contract

```python
"""
Counter Contract
A simple counter that can be incremented and read.
"""

from stdlib import abi, events, storage

KEY_COUNT = b"counter:value"
U128_MAX = (1 << 128) - 1

def _load_u128(key: bytes) -> int:
    """Load a u128 value from storage."""
    raw = storage.get(key)
    if raw is None or len(raw) == 0:
        return 0
    if len(raw) > 16:
        abi.revert(b"BAD_STORED_LENGTH")
    return int.from_bytes(raw.rjust(16, b"\\x00"), "big")

def _store_u128(key: bytes, value: int) -> None:
    """Store a u128 value to storage."""
    abi.require(0 <= value <= U128_MAX, b"U128_OVERFLOW")
    storage.set(key, value.to_bytes(16, "big"))

def get() -> int:
    """Get the current counter value."""
    return _load_u128(KEY_COUNT)

def inc(by: int = 1) -> int:
    """Increment the counter."""
    abi.require(1 <= by <= 1_000_000, b"INCREMENT_OUT_OF_BOUNDS")
    
    current = _load_u128(KEY_COUNT)
    new_value = current + by
    
    abi.require(new_value <= U128_MAX, b"COUNTER_OVERFLOW")
    _store_u128(KEY_COUNT, new_value)
    
    events.emit("Incremented", {"by": by, "newValue": new_value})
    
    return new_value

def deploy(initial: int = 0) -> None:
    """Constructor - initialize counter."""
    abi.require(0 <= initial <= U128_MAX, b"INVALID_INITIAL_VALUE")
    _store_u128(KEY_COUNT, initial)
```

### Step 3: Add a Manifest (Optional)

For proper deployment, create a `manifest.json` file:

1. Click **"+ New"** in the File Tree
2. Filename: `manifest.json`
3. File type: **JSON (.json)**
4. Click **Create**

The IDE will generate a basic manifest structure for you.

## Building Your Contract

### Step 4: Compile

1. Click the **"🔨 Build"** button in the Build Panel
2. Watch the compilation output
3. If successful, you'll see:
   - ✅ Build successful message
   - Code hash
   - Code size
   - Gas upper bound estimate
   - Function list extracted from your code

#### Build Output Example

```
✅ Build successful!

📦 Build Artifacts
Code Hash: 0x1234567890abcdef...
Size: 512 bytes
Gas Upper Bound: 45,000
Functions: 3

✓ Mock compilation successful
  Code hash: 0x1234567890abcdef1234567890abcdef12345678...
  Size: 512 bytes
  Functions: 3
```

#### Troubleshooting Build Errors

Common issues:
- **Syntax errors**: Check Python syntax
- **Missing imports**: Ensure `from stdlib import ...` is correct
- **Invalid manifest**: Verify JSON structure

## Connecting Your Wallet

### Step 5: Connect Wallet

1. Look for the **Wallet Status** widget (top right)
2. If wallet is not detected:
   - Install the Animica Wallet Extension
   - Refresh the page
3. Click **"Connect Wallet"** button
4. Approve the connection in the wallet popup
5. Once connected, you'll see:
   - Your account address (truncated)
   - Current network (Local/Devnet/Mainnet)
   - Green checkmark indicator

### Network Switching

To switch networks:
1. Open the wallet extension
2. Select a different network from the dropdown
3. The IDE will automatically update

Supported networks:
- **Local** (127.0.0.1:8545) - For development
- **Devnet** - For testing
- **Mainnet** - Production (shows warning)

## Deploying Your Contract

### Step 6: Deploy to Chain

1. Ensure your contract is built successfully
2. Navigate to the **Deploy** page
3. Configure deployment:
   - **From Account**: Auto-filled from connected wallet
   - **Network**: Current network displayed
   - **Constructor Arguments**: Enter if your `deploy()` function has parameters
   - **Gas Limit**: Auto-estimated or manually set
4. Click **"Deploy Contract"**
5. Confirm the transaction in your wallet
6. Wait for the transaction to be mined
7. Copy the contract address for future interaction

#### Deploy Panel Fields

| Field | Description | Example |
|-------|-------------|---------|
| Contract Address | Will be filled after deployment | `anim1qpzry9x8gf2tvdw0s3jn54khce6mua7l...` |
| Transaction Hash | Deployment tx hash | `0x123...` |
| Gas Used | Actual gas consumed | 98,456 |
| Block Number | Block where tx was included | 12345 |

## Interacting with Contracts

### Step 7: Call Contract Methods

1. Navigate to the **Interact** page
2. Enter the contract address (or load from recent deploys)
3. The IDE will load the contract's ABI
4. You'll see a list of available functions:
   - **View functions** (read-only, no gas cost)
   - **Write functions** (modify state, requires tx)

#### Calling View Functions

Example: Call `get()` on the Counter contract

1. Find `get()` in the function list
2. Click **"Call"**
3. Result appears immediately:
   ```
   Result: 0
   ```

#### Calling Write Functions

Example: Call `inc(by: 1)` on the Counter contract

1. Find `inc` in the function list
2. Enter parameters:
   - `by`: `1`
3. Click **"Send Transaction"**
4. Confirm in wallet
5. Wait for confirmation
6. View the result:
   ```
   Transaction: 0xabc...
   Status: Success
   Gas Used: 21,543
   Events:
     - Incremented(by=1, newValue=1)
   ```

## Advanced Features

### Project Management

#### Saving Projects

Projects are automatically saved to browser IndexedDB. All files, build artifacts, and state persist across sessions.

#### Creating Multiple Files

You can organize contracts with multiple files:
- `contract.py` - Main contract
- `helpers.py` - Helper functions
- `manifest.json` - Deployment manifest
- `README.md` - Documentation

#### Deleting Files

1. Hover over a file in the File Tree
2. Click the **✕** button
3. Confirm deletion

### Contract Templates

The IDE includes pre-built templates:

1. **Counter**: Simple counter with increment/get operations
2. **Hello World**: Minimal contract with message storage
3. **Token**: Basic token with balances and transfers

To use a template:
1. Create a new file
2. The IDE will offer template options
3. Select a template to auto-populate the file

### Local Simulation

Before deploying, you can simulate contract execution locally:
1. Build your contract
2. Use the simulation panel (coming soon)
3. Test function calls without gas costs
4. View state changes and events

### Viewing Logs and Events

After a transaction:
1. Click on the transaction hash
2. View detailed logs
3. See emitted events with decoded parameters
4. Inspect storage changes (if available)

## Keyboard Shortcuts

### Editor
- `Ctrl/Cmd + S`: Save (auto-save is enabled)
- `Ctrl/Cmd + F`: Find
- `Ctrl/Cmd + H`: Replace
- `Ctrl/Cmd + /`: Toggle comment
- `Ctrl/Cmd + Z`: Undo
- `Ctrl/Cmd + Shift + Z`: Redo

### IDE
- `Ctrl/Cmd + B`: Build contract
- `Ctrl/Cmd + D`: Deploy
- `Ctrl/Cmd + I`: Interact

## Settings and Configuration

### RPC Configuration

To connect to a custom node:
1. Navigate to **Settings** page
2. Enter custom RPC URL
3. Test connection
4. Save

Example RPCs:
- Local: `http://127.0.0.1:8545/rpc`
- Devnet: `https://devnet.animica.org/rpc`
- Mainnet: `http://144.126.133.21:8545/rpc`

### Editor Preferences

Customize your editor:
- Theme: Light / Dark
- Font size: 12-20px
- Tab size: 2 or 4 spaces
- Word wrap: On / Off

## Tips and Best Practices

### 1. Write Deterministic Code
- No randomness (use chain-provided randomness)
- No wall-clock time (use block timestamps)
- No file I/O or network calls

### 2. Handle Errors Gracefully
```python
abi.require(condition, b"ERROR_MESSAGE")
```

### 3. Use Events for Logging
```python
events.emit("ActionPerformed", {
    "user": caller,
    "amount": value
})
```

### 4. Test on Devnet First
Always test on devnet before mainnet deployment.

### 5. Validate Inputs
Check all user inputs for validity:
```python
abi.require(amount > 0, b"AMOUNT_MUST_BE_POSITIVE")
abi.require(amount <= MAX_AMOUNT, b"AMOUNT_TOO_LARGE")
```

### 6. Gas Optimization
- Minimize storage writes
- Batch operations when possible
- Use appropriate data types

## Troubleshooting

### Wallet Not Connecting

**Problem**: "Wallet Not Found" message

**Solutions**:
1. Install the Animica Wallet Extension
2. Refresh the page
3. Check browser console for errors
4. Ensure extension is enabled

### Compilation Errors

**Problem**: Build fails with errors

**Solutions**:
1. Check Python syntax
2. Verify stdlib imports
3. Ensure manifest.json is valid JSON
4. Review diagnostics in build output

### Transaction Failures

**Problem**: Deploy or call transaction fails

**Solutions**:
1. Check account balance (sufficient for gas)
2. Verify network is correct
3. Ensure gas limit is adequate
4. Review transaction error message

### "Mock compiler" Warning

**Problem**: Build output shows mock compiler warning

**Explanation**: The IDE is using a mock compiler because studio-wasm (full Python VM compiler) is not available. This is normal for development. The mock compiler:
- Generates valid bytecode structure
- Extracts function signatures
- Produces deterministic hashes
- Sufficient for testing the IDE workflow

For production use, integrate the real studio-wasm compiler.

## Security Considerations

### ⚠️ Never Share Private Keys
The IDE never asks for or stores private keys. All signing is done by the wallet extension.

### ⚠️ Verify Contracts Before Deploying
- Review all code carefully
- Test on devnet first
- Verify contract addresses

### ⚠️ Mainnet Warning
When connecting to mainnet, you'll see a warning. Only proceed if you're deploying production contracts.

### ⚠️ Browser Storage
Projects are stored in browser IndexedDB. Clear browser data will delete projects. Export important projects as backups.

## Support and Resources

### Documentation
- [Animica Docs](https://docs.animica.org)
- [Python VM Specification](../../vm_py/specs/)
- [ABI Schema](../../spec/abi.schema.json)
- [Manifest Schema](../../spec/manifest.schema.json)

### Community
- Discord: [Animica Community](https://discord.gg/animica)
- GitHub: [animicaorg/all](https://github.com/animicaorg/all)
- Twitter: [@animica](https://twitter.com/animica)

### Example Contracts
Check the `contracts/examples/` directory for more contract examples:
- Counter
- Token
- Escrow
- Oracle
- Multisig
- And more...

## FAQ

**Q: Can I use this IDE offline?**
A: Yes, once loaded. However, you need network access to deploy and interact with contracts on-chain.

**Q: What happens if I close the browser?**
A: Your projects are saved in IndexedDB. They'll be available when you reopen the IDE.

**Q: Can I collaborate with others?**
A: Currently, the IDE is single-user. For collaboration, export your project and share the files.

**Q: How do I export a project?**
A: Use the Export feature (coming soon) or manually copy files from the IDE.

**Q: Can I import existing contracts?**
A: Yes, create new files and paste your code. Ensure the manifest matches your project structure.

**Q: What's the gas limit for transactions?**
A: It varies by operation. The IDE auto-estimates. For complex operations, you may need to increase it manually.

**Q: How do I update a deployed contract?**
A: Contracts are immutable. To update, deploy a new version and migrate data if needed.

## Changelog

### v0.1.0 (Current)
- Initial release
- Monaco editor integration
- Build system with mock compiler
- Wallet connection (window.animica)
- File management (create, edit, delete)
- Project persistence (IndexedDB)
- Contract templates (Counter, Hello, Token)
- Build panel with diagnostics
- Basic deploy and interact scaffolding

### Planned Features
- Real studio-wasm compiler integration
- Full deploy panel with parameter input
- ABI-driven interact panel
- Local simulation before deployment
- Project export/import
- Code snippets and autocomplete
- Gas profiler
- Multi-account support
- Dark/light theme toggle
- Contract verification

---

**Version**: 0.1.0  
**Last Updated**: February 2026  
**License**: See LICENSE file
