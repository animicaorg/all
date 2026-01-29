# Qt Wallet Diagnostics - Testing Guide

## Pre-Build Testing Checklist

### Static Analysis
- [x] All source files compile (CMake configuration)
- [x] No syntax errors in headers
- [x] Proper include guards
- [x] Qt MOC compatibility (Q_OBJECT macros)
- [x] Signal/slot declarations

### Code Review
- [x] Memory management (no leaks)
- [x] Cross-platform paths (QDir usage)
- [x] Thread safety (QMutex where needed)
- [x] Resource cleanup (parent-child relationships)

## Build Testing

### Prerequisites
```bash
# Install Qt5 or Qt6
sudo apt install qt6-base-dev qt6-base-private-dev  # Ubuntu/Debian
# OR
brew install qt6  # macOS

# Install OpenSSL
sudo apt install libssl-dev  # Ubuntu/Debian
brew install openssl  # macOS
```

### Build Steps
```bash
cd wallet-qt
cmake -B build -S .
cmake --build build -j$(nproc)
```

Expected output:
- All 18 diagnostics source files compile
- 3 test executables created
- Main wallet executable links successfully

### Unit Testing
```bash
cd build
ctest --output-on-failure --verbose
```

Expected tests:
- test_redactor (8 test cases)
- test_allowlist (7 test cases)
- test_executor (6 test cases)
- test_keystore_security (existing)
- test_wallet_engine (existing)
- test_walletdatabase (existing)

## Integration Testing

### 1. Diagnostics Window Launch
```bash
./build/bin/animica-wallet
```

**Test:** Node → Diagnostics (Ctrl+D)
- ✅ Diagnostics window opens
- ✅ 3 tabs visible: Console, Node Status, Logs
- ✅ Window is resizable
- ✅ Close button works

### 2. Console Tab Tests

#### Basic Commands
```
node status
peer list
sync status
mempool stats
```

**Expected:**
- ✅ Commands execute
- ✅ Output appears in browser
- ✅ Timestamps shown
- ✅ JSON is pretty-printed
- ✅ Green prompt for commands
- ✅ Gray metadata for timing

#### Command History
1. Type `node status`, press Enter
2. Type `peer list`, press Enter
3. Press Up arrow twice

**Expected:**
- ✅ First up shows `peer list`
- ✅ Second up shows `node status`
- ✅ Down arrow cycles forward

#### Autocomplete
1. Type `node ` (with space)
2. Press Tab or start typing

**Expected:**
- ✅ Dropdown shows: status, head, block, tx
- ✅ Selecting completes command

#### Role Changes
1. Check "Operator Mode"
2. Type `sync `

**Expected:**
- ✅ Autocomplete now shows: force, pause, resume
- ✅ Role label updates to "Role: Operator"

#### Command Rejection
1. Uncheck Operator and Developer modes
2. Type `sync force`
3. Press Enter

**Expected:**
- ✅ Error: "Command not allowed for current role (User)"
- ✅ Command not executed

### 3. Node Status Tab Tests

#### Auto-Refresh
1. Navigate to Node Status tab
2. Wait 5 seconds

**Expected:**
- ✅ Status updates automatically
- ✅ "Last update" timestamp refreshes
- ✅ All panels show data or "N/A"

#### Manual Refresh
1. Click "Refresh Now"

**Expected:**
- ✅ Status updates immediately
- ✅ Timestamp updates

#### Action Buttons (User Role)
1. Ensure Operator mode is OFF
2. Check Bootstrap, Force Sync, Pause, Resume buttons

**Expected:**
- ✅ All action buttons are disabled
- ✅ Tooltips explain requirement

#### Action Buttons (Operator Role)
1. Enable Operator mode
2. Click "Force Sync"

**Expected:**
- ✅ Confirmation dialog appears
- ✅ Dialog explains action
- ✅ "Yes" executes, "No" cancels
- ✅ Result message shown
- ✅ Status refreshes after action

### 4. Logs Tab Tests

#### Log Display
1. Navigate to Logs tab
2. (Ensure node is running and producing logs)

**Expected:**
- ✅ Logs appear in browser
- ✅ Auto-scroll enabled by default
- ✅ Colors: ERROR=red, WARNING=orange, INFO=black, DEBUG=gray

#### Filtering
1. Set level filter to "ERROR"

**Expected:**
- ✅ Only ERROR lines shown
- ✅ Other lines hidden

2. Set component filter to "p2p"

**Expected:**
- ✅ Only lines containing "p2p" shown

3. Type "sync" in search box

**Expected:**
- ✅ Only lines containing "sync" shown

#### Pause/Resume
1. Click "Pause"

**Expected:**
- ✅ Button text changes to "Resume"
- ✅ No new logs appear (even if node emits them)

2. Click "Resume"

**Expected:**
- ✅ Button text changes to "Pause"
- ✅ New logs appear again

#### Export
1. Click "Export Logs"
2. Choose filename
3. Click Save

**Expected:**
- ✅ File save dialog appears
- ✅ File is created
- ✅ File contains logs (with redaction)
- ✅ Confirmation message shown

### 5. Security Tests

#### Secret Redaction
1. Console tab: `rpc call node.getStatus`
2. Check output for any sensitive data patterns

**Expected:**
- ✅ No passwords visible
- ✅ No tokens visible
- ✅ No private keys visible
- ✅ "***REDACTED***" appears where needed

#### Confirmation Dialogs
1. Operator mode ON
2. Click "Bootstrap"

**Expected:**
- ✅ Dialog appears before execution
- ✅ Dialog explains consequences
- ✅ "No" cancels without execution

#### Audit Logging
1. Operator mode ON
2. Execute operator action
3. Check NodeController signals (if monitoring)

**Expected:**
- ✅ `actionLogged` signal emitted
- ✅ Signal includes username@hostname
- ✅ Signal includes action name and result

## Performance Testing

### Memory Usage
1. Open Diagnostics window
2. Switch between tabs 20 times
3. Execute 50 commands in Console
4. Let Status auto-refresh for 5 minutes
5. Add 20,000 log lines to Logs tab

**Expected:**
- ✅ No memory leaks (use valgrind or similar)
- ✅ Memory usage stays bounded
- ✅ No crashes or freezes

### Responsiveness
1. Execute long-running command
2. Try to interact with UI

**Expected:**
- ✅ UI remains responsive during execution
- ✅ Can switch tabs
- ✅ Can close window

## Error Handling Tests

### Node Offline
1. Stop node
2. Open Diagnostics
3. Try commands

**Expected:**
- ✅ Console shows connection errors
- ✅ Status shows "N/A (Node offline)"
- ✅ No crashes

### Invalid Commands
1. Type `invalid command`
2. Press Enter

**Expected:**
- ✅ Error message shown
- ✅ No crash
- ✅ Can continue using console

### Timeout Scenario
1. (Simulate slow node or network)
2. Execute command

**Expected:**
- ✅ Timeout message after configured time
- ✅ Can execute more commands
- ✅ No hanging

## Cross-Platform Testing

### macOS
- [ ] Build succeeds
- [ ] All tests pass
- [ ] UI renders correctly
- [ ] Keyboard shortcuts work
- [ ] File dialogs work

### Windows
- [ ] Build succeeds
- [ ] All tests pass
- [ ] UI renders correctly
- [ ] Keyboard shortcuts work (Ctrl not Command)
- [ ] File dialogs work
- [ ] Path handling works (backslashes)

### Linux
- [ ] Build succeeds
- [ ] All tests pass
- [ ] UI renders correctly
- [ ] Keyboard shortcuts work
- [ ] File dialogs work

## Regression Testing

After any changes, verify:
- [ ] Existing wallet features still work
- [ ] Node control widget still works
- [ ] Main window menu still works
- [ ] Can still start/stop node
- [ ] No new compilation warnings

## Known Issues to Test

1. **Qt Version Compatibility**
   - Test with Qt5 (5.15+)
   - Test with Qt6 (6.2+)

2. **Large Outputs**
   - Verify 2MB limit works
   - Verify 20k line limit works
   - Verify truncation message appears

3. **Role Persistence**
   - Enable Operator mode
   - Close wallet
   - Reopen wallet
   - Verify Operator mode still enabled

## Test Reporting

For each test session, record:
- Date and time
- Qt version
- OS and version
- Commit hash
- Test results (pass/fail)
- Any issues found

Example:
```
Date: 2026-01-29
Qt: 6.5.0
OS: Ubuntu 22.04
Commit: 63059cd3
Results: 45/45 tests passed
Issues: None
```

## Automated Testing

Potential automation:
- Unit tests: CTest (already done)
- UI tests: Qt Test with QTest::qWait()
- Integration: Python scripts calling RPC
- Performance: Valgrind, memory profilers

## Success Criteria

✅ All unit tests pass  
✅ Diagnostics window opens without errors  
✅ All tabs render correctly  
✅ Commands execute and show output  
✅ Role system works (allowlist enforced)  
✅ Confirmation dialogs appear for operator actions  
✅ Auto-refresh works on Status tab  
✅ Log filtering works on Logs tab  
✅ Export functions work (Console and Logs)  
✅ No memory leaks detected  
✅ Cross-platform compatibility verified  
✅ Existing features not broken  

---

**Note:** Some tests require a running Animica node. For full testing, ensure node is built and accessible at the bundled path.
