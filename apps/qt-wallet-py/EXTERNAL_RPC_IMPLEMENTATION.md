# External RPC Interface Implementation Summary

## Overview
This implementation adds a secure external RPC interface to the Animica Qt Wallet, allowing local applications to request wallet actions with user approval. All requirements from the problem statement have been met.

## Files Added

### Core Components
1. **`src/animica_qt_wallet/walletd/approval_queue.py`** (207 lines)
   - Thread-safe queue for managing approval requests
   - Request persistence to disk (JSON)
   - Auto-expiration of old requests
   - Max pending limit enforcement

2. **`src/animica_qt_wallet/walletd/rate_limiter.py`** (68 lines)
   - Token bucket rate limiting
   - Per-client tracking
   - Configurable requests/minute and burst size

3. **`src/animica_qt_wallet/walletd/app_allowlist.py`** (147 lines)
   - Application allowlist management
   - Deny-by-default policy
   - Optional auto-approve for trusted apps
   - Persistent state storage

4. **`src/animica_qt_wallet/ui/approval_dialog.py`** (144 lines)
   - Qt dialog for approval requests
   - Shows requester info (process, PID, IP)
   - Displays transaction details
   - Warning messages

### Documentation & Examples
5. **`docs/EXTERNAL_RPC_API.md`** (190 lines)
   - Complete API reference
   - Security model documentation
   - Request/response examples
   - Error codes

6. **`example_external_rpc.py`** (150 lines)
   - Sample client demonstrating usage
   - Token loading
   - Account requests
   - Transaction sending

### Tests
7. **`tests/test_approval_queue.py`** (162 lines, 9 tests)
8. **`tests/test_rate_limiter.py`** (94 lines, 6 tests)
9. **`tests/test_app_allowlist.py`** (130 lines, 10 tests)

## Files Modified

1. **`src/animica_qt_wallet/walletd/server.py`** (+226 lines)
   - Added `/external` endpoint handler
   - Implemented `dispatch_external()` function
   - Added wallet methods: requestAccounts, signTransaction, sendTransaction, getChainId
   - Added approval methods: approval.list, approval.respond
   - Integrated ApprovalQueue, AppAllowlist, and RateLimiter

2. **`src/animica_qt_wallet/walletd/config.py`** (+8 lines)
   - Added `resolve_approval_queue_path()`
   - Added `resolve_app_allowlist_path()`

3. **`src/animica_qt_wallet/ui/main_window.py`** (+62 lines)
   - Added approval polling timer (1 second interval)
   - Implemented `_check_pending_approvals()` method
   - Integrated ApprovalDialog
   - Tracks shown approvals to avoid duplicates

4. **`pyproject.toml`** (+1 line)
   - Added `psutil>=5.9.0` dependency for process detection

5. **`README.md`** (+25 lines)
   - Added External RPC Interface section
   - Quick example usage
   - Link to API documentation

## API Methods

### External Methods (localhost + token auth)
- **`wallet_getChainId`** - Get chain ID (no approval)
- **`wallet_requestAccounts`** - Get wallet addresses (requires approval)
- **`wallet_signTransaction`** - Sign a transaction (requires approval)
- **`wallet_sendTransaction`** - Sign and send a transaction (requires approval)

### Internal Methods (UI only)
- **`approval.list`** - List pending approval requests
- **`approval.respond`** - Respond to approval request (approve/deny)

## Security Features

1. **Localhost-only**: Only accepts connections from 127.0.0.1 or ::1
2. **Token authentication**: Bearer token required for all requests
3. **App allowlist**: Deny-by-default with optional allowlist
4. **Rate limiting**: 10 requests/minute, 5 burst
5. **User approval**: All signing/sending requires explicit approval
6. **Process identification**: Shows process name and PID in approval dialog
7. **Request timeout**: Pending requests expire after 2 minutes

## Approval Flow

1. External app makes RPC call to `/external`
2. Walletd validates localhost + token
3. Rate limiter checks client quota
4. App allowlist checks permission
5. For approval-required methods:
   - Request added to queue
   - Client waits for response (up to 2 minutes)
6. UI polls queue every 1 second
7. ApprovalDialog shown to user with details
8. User approves or denies
9. Response sent to walletd
10. Client receives result or error

## Testing

All 25 tests passing:
- **Approval queue**: create, list, approve, deny, expire, cleanup, persistence
- **Rate limiter**: per-client limits, burst protection, reset
- **App allowlist**: default policies, allow/deny, auto-approve, persistence

## Usage Example

```python
import requests

# Load token
token = Path("~/.animica-wallet/walletd.token").read_text().strip()

# Make request
response = requests.post(
    "http://127.0.0.1:17834/external",
    json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "wallet_requestAccounts",
        "params": {}
    },
    headers={"Authorization": f"Bearer {token}"}
)

# User approves in wallet UI
accounts = response.json()["result"]
```

## Integration Points

- **Walletd server**: New `/external` route alongside existing `/` route
- **Main window**: New polling timer for approvals
- **State management**: ApprovalQueue, AppAllowlist, RateLimiter added to WalletdState
- **Dependencies**: Added psutil for process identification

## Future Enhancements (Not Required)

- WebSocket subscription for approval events (instead of polling)
- Signature request caching
- Multi-sig support
- Hardware wallet integration via approval flow
- Granular permissions (e.g., spending limits)
- Transaction simulation preview

## Acceptance Criteria ✅

All requirements from the problem statement are met:

✅ **Expose JSON-RPC namespace** - `/external` endpoint with wallet methods  
✅ **Interactive approval required** - ApprovalDialog shows app info and tx details  
✅ **Internal request queue** - ApprovalQueue with persistence and polling  
✅ **Security: localhost-only + token** - Enforced in handle_external_rpc  
✅ **Security: allowlist apps** - AppAllowlist with deny-by-default  
✅ **Security: rate limiting** - RateLimiter with per-client tracking  
✅ **Sample script** - example_external_rpc.py demonstrates full flow

## Line Counts

- **Total lines added**: ~1,200
- **Total lines modified**: ~300
- **Total files created**: 9
- **Total files modified**: 5
- **Test coverage**: 25 tests (approval queue, rate limiter, allowlist)
