# Wallet Extension RPC Fix - Visual Summary

## 🔴 BEFORE: Silent Failures

```
User clicks "Send" in wallet
         ↓
Background builds transaction
         ↓
RPC call: tx.sendRawTransaction
         ↓
❌ Error: Invalid params (code -32602)
         ↓
Generic error message shown:
"RpcResponseError: Invalid params (code -32602)"
         ↓
🤔 No way to see what was actually sent
🤔 No documentation on expected format
🤔 Debug flags not documented
```

**Console Output:**
```
[wallet-bg] handleSendTransaction failed: RpcResponseError: Invalid params (code -32602)
```

**Developer Experience:**
- ❌ Can't see the actual request payload
- ❌ Don't know if params are array or object
- ❌ No guidance on common mistakes
- ❌ Have to dig through node code to understand signature

---

## 🟢 AFTER: Clear Diagnostics

```
User clicks "Send" in wallet
         ↓
Background builds transaction
         ↓
RPC call: tx.sendRawTransaction with { rawTx: "0x..." }
         ↓
If error occurs:
  → Full request logged AUTOMATICALLY
  → Error code explained
  → Documentation referenced
         ↓
✅ Developer can see exactly what was sent
✅ Hint points to troubleshooting guide
✅ Can quickly identify the problem
```

**Console Output (Automatic):**
```javascript
[wallet-rpc] RPC ERROR {
  url: 'http://144.126.133.21:8545/rpc',
  method: 'tx.sendRawTransaction',
  fullRequest: {
    jsonrpc: '2.0',
    id: 1234567890,
    method: 'tx.sendRawTransaction',
    params: { rawTx: '0xa2627478a96176026367...' }
  },
  responseError: {
    code: -32602,
    message: 'Invalid params',
    data: undefined
  },
  hint: 'Code -32602 means params shape/type mismatch. See apps/wallet-extension/docs/RPC_TRANSACTION_SUBMISSION.md for common causes.'
}
```

**Developer Experience:**
- ✅ See the **exact request** that was sent
- ✅ Know the params format used: `{ rawTx: "0x..." }`
- ✅ Get a **direct link** to troubleshooting docs
- ✅ Compare against documented examples

---

## 📚 Documentation Improvements

### New Files

**1. `docs/RPC_TRANSACTION_SUBMISSION.md` (250+ lines)**
```
✅ Node RPC signature explained
✅ Both valid forms documented (array vs object)
✅ 5 common mistakes with examples
✅ Error code reference table
✅ Debug logging instructions
✅ Testing guide
```

**2. `README.md` (updated)**
```
✅ Debug logging section added
✅ How to enable VITE_DEBUG_RPC_PAYLOADS
✅ How to use runtime global flag
✅ What each debug mode shows
```

**3. `WALLET_EXTENSION_RPC_FIX_SUMMARY.md` (200+ lines)**
```
✅ Complete investigation findings
✅ Root cause analysis
✅ Solution implementation details
✅ Testing results
✅ Usage instructions
```

### Updated Comments

**`src/core/rpc/client.ts`**
```typescript
// BEFORE
// Node signature: rpc/methods/tx.py defines tx.sendRawTransaction(rawTx: str),
// and dispatcher keyword-binding accepts params object form { rawTx: '0x...' }.

// AFTER
// NODE RPC SIGNATURE (rpc/methods/tx.py):
//   def tx_send_raw_transaction(rawTx: str) -> t.Any
//
// The RPC dispatcher (rpc/jsonrpc.py _bind_call_args) accepts params in two forms:
//   1. Array form:  params: ["0xabcd..."]  → binds to positional arg rawTx
//   2. Object form: params: { rawTx: "0xabcd..." }  → binds to keyword arg rawTx
//
// We use object form for clarity and validation.
```

---

## 🧪 Test Improvements

### Before
```typescript
describe('tx.sendRawTransaction request shape', () => {
  it('builds JSON-RPC payload using object params with rawTx', () => { /* ... */ });
  it('prints a sample request payload for dev debugging', () => { /* ... */ });
});

// 2 tests
```

### After
```typescript
describe('tx.sendRawTransaction request shape', () => {
  it('builds JSON-RPC payload using object params with rawTx', () => { /* ... */ });
  it('prints a sample request payload for dev debugging', () => { /* ... */ });
  it('validates that rawTx is a hex string', () => { /* ... */ });
  it('documents the node RPC signature for reference', () => { /* ... */ });
  it('prevents common mistakes that cause -32602 errors', () => { /* ... */ });
});

// 5 tests - shows both valid forms and common mistakes
```

**Sample Test Output:**
```
[dev] Both forms are valid per node dispatcher:
  Array form:  {"jsonrpc":"2.0","id":1,"method":"tx.sendRawTransaction","params":["0xabcd1234"]}
  Object form: {"jsonrpc":"2.0","id":2,"method":"tx.sendRawTransaction","params":{"rawTx":"0xabcd1234"}}

[dev] WRONG (double-wrapped): {"jsonrpc":"2.0","id":1,"method":"tx.sendRawTransaction","params":{"params":["0xabcd1234"]}}
[dev] WRONG (wrong key): {"jsonrpc":"2.0","id":2,"method":"tx.sendRawTransaction","params":{"tx":"0xabcd1234"}}
[dev] CORRECT: {"jsonrpc":"2.0","id":3,"method":"tx.sendRawTransaction","params":{"rawTx":"0xabcd1234"}}
```

---

## 🎯 Impact Summary

### Lines of Code
- **Documentation**: +500 lines
- **Test Code**: +60 lines  
- **Logging Code**: +10 lines
- **Comments**: +30 lines
- **Total**: +600 lines (all additive, no breaking changes)

### Test Coverage
- **Before**: 2 tests for RPC request shape
- **After**: 5 tests for RPC request shape
- **Result**: 18/18 RPC tests passing

### Security
- **CodeQL Scan**: 0 alerts
- **No vulnerabilities** introduced or discovered
- **Pure additive changes** (logging + docs)

### Developer Experience
| Aspect | Before | After |
|--------|--------|-------|
| Error visibility | ❌ Generic message | ✅ Full request logged |
| Documentation | ❌ Scattered | ✅ Comprehensive guide |
| Debug mode | ⚠️ Undocumented | ✅ Clearly documented |
| Common mistakes | ❌ Unknown | ✅ Listed with examples |
| Troubleshooting | ❌ Guess and check | ✅ Step-by-step guide |

---

## 🚀 How to Use

### For Development
```bash
# Enable debug mode (see ALL requests/responses)
VITE_DEBUG_RPC_PAYLOADS=1 pnpm build
```

### For Production
```javascript
// -32602 errors are ALWAYS logged automatically
// No configuration needed!
// Just open service worker console to see the details
```

### For Troubleshooting
1. See -32602 error in console
2. Check the `fullRequest` field in the logged error
3. Compare against examples in `docs/RPC_TRANSACTION_SUBMISSION.md`
4. Verify params match: `{ rawTx: "0xabcd..." }`

---

## ✅ Result

The wallet extension code was **already correct**. This PR adds:

1. ✅ **Better diagnostic visibility** - Always-on error logging for -32602
2. ✅ **Comprehensive documentation** - 500+ lines covering all aspects
3. ✅ **Expanded test coverage** - 5 tests with realistic examples
4. ✅ **Clear instructions** - How to enable debug mode and troubleshoot

**Any future -32602 errors will be immediately diagnosable** with the full request details automatically logged to the console.
