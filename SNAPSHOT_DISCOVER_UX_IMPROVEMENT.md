# Snapshot Discovery UX Improvement

## Problem
When users ran `animica snapshot discover` with peers connected but no snapshots available, the command would exit with error code 1, even though the operation succeeded (peers were queried successfully via P2P protocol).

### Example Output (Before)
```bash
$ animica snapshot discover
🔍 Discovering snapshots from connected peers via P2P protocol...

❌ Connected to 2 peer(s), but none have snapshots available.

💡 Troubleshooting:
  1. You're connected to 2 peer(s), but they don't have snapshots
  2. Peers need to create snapshots first (animica snapshot create)
  3. Try connecting to more peers: animica peer add <address>
  4. Wait for peers to sync and create snapshots

$ echo $?
1
```

## Solution
Distinguish between actual errors and informational states:

### Error Cases (Exit Code 1)
- No peers connected
- P2P service unavailable
- RPC call failed

### Informational Cases (Exit Code 0)
- Peers connected but no snapshots available
- Query succeeded, just no results

## Changes

### Code Change
**File:** `python/animica/cli/snapshot.py`

**Before:**
```python
if not snapshots:
    message = result.get("message", "No snapshots found")
    typer.echo(f"\n❌ {message}")
    
    typer.echo("\n💡 Troubleshooting:")
    # ... tips
    raise typer.Exit(code=1)  # Always exit with error
```

**After:**
```python
if not snapshots:
    message = result.get("message", "No snapshots found")
    
    if peer_count == 0:
        # No peers connected - this is an error condition
        typer.echo(f"\n❌ {message}")
        typer.echo("\n💡 Troubleshooting:")
        # ... tips for connecting peers
        raise typer.Exit(code=1)
    else:
        # Peers connected but no snapshots - informational, not an error
        typer.echo(f"\nℹ️  {message}")
        typer.echo("\n💡 Tips:")
        # ... tips about waiting for snapshots
        return  # Exit with code 0
```

### Test Updates
**File:** `python/animica/cli/tests/test_snapshot_peer_discovery.py`

Updated tests to:
1. Use new `snapshot.discoverFromPeers` RPC method
2. Expect exit code 0 for "peers but no snapshots" case
3. Expect exit code 1 for "no peers" case

## Behavior Matrix

| Scenario | Peers | Snapshots | Exit Code | Message Type | Section |
|----------|-------|-----------|-----------|--------------|---------|
| No peers connected | 0 | N/A | 1 | ❌ Error | Troubleshooting |
| Peers, no snapshots | >0 | 0 | 0 | ℹ️  Info | Tips |
| Peers with snapshots | >0 | >0 | 0 | ✅ Success | Results |
| RPC/P2P error | N/A | N/A | 1 | ❌ Error | Troubleshooting |

## Example Output (After)

### Case 1: Peers Connected, No Snapshots (Informational)
```bash
$ animica snapshot discover
🔍 Discovering snapshots from connected peers via P2P protocol...

ℹ️  Connected to 2 peer(s), but none have snapshots available.

💡 Tips:
  - You're connected to 2 peer(s), but they don't have snapshots yet
  - Peers need to create snapshots first (animica snapshot create)
  - Try connecting to more peers: animica peer add <address>
  - Wait for peers to sync and create snapshots

$ echo $?
0
```

### Case 2: No Peers Connected (Error)
```bash
$ animica snapshot discover
🔍 Discovering snapshots from connected peers via P2P protocol...

❌ No peers connected. Connect to peers first using 'animica peer add <address>'.

💡 Troubleshooting:
  1. Check peer connections: animica peer list
  2. Connect to peers: animica peer add <address>
  3. Ensure your node's P2P service is running
  4. Check firewall settings if running your own node

$ echo $?
1
```

### Case 3: Snapshots Found (Success)
```bash
$ animica snapshot discover
🔍 Discovering snapshots from connected peers via P2P protocol...

✅ Found 3 snapshot(s) from 2 peer(s) via P2P

🏆 Best snapshot (highest height):
  Chain ID:         1
  Height:           2000
  Hash:             0xbbb...
  Blocks:           2001
  Accounts:         100
  Size:             20.30 MB
  Source Peer:      peer_abc123...

💡 To use this snapshot for fast sync:
  1. Ensure ANIMICA_SNAPSHOT_SYNC_ENABLED=true (default)
  2. Restart your node - it will auto-discover and use this snapshot
  3. The node automatically queries P2P peers during startup

$ echo $?
0
```

## Benefits

1. **More Accurate Exit Codes**
   - Exit code 1 reserved for actual errors
   - Exit code 0 for successful operations (even with no results)

2. **Better Scriptability**
   - Scripts can differentiate between:
     - Connection failures (exit 1)
     - No results available (exit 0)

3. **Clearer User Communication**
   - ℹ️ emoji for informational messages
   - ❌ emoji only for actual errors
   - "Tips" vs "Troubleshooting" language

4. **Consistency**
   - Matches common CLI conventions
   - Similar to tools like `grep` (exit 0 when no matches, exit 2 for errors)

## Files Modified

1. `python/animica/cli/snapshot.py`
   - Updated `discover` command logic
   - Split no-snapshots case into error vs informational

2. `python/animica/cli/tests/test_snapshot_peer_discovery.py`
   - Updated `test_snapshot_discover_no_snapshots` (exit 0)
   - Updated `test_snapshot_discover_no_peers_connected` (exit 1)
   - Both tests now use `snapshot.discoverFromPeers` RPC method

## Testing

### Manual Verification
✅ Code correctly handles "no peers" as error (exit 1)
✅ Code correctly handles "peers but no snapshots" as info (exit 0)
✅ Code correctly handles "peers with snapshots" as success (exit 0)
✅ Uses appropriate emoji (❌ for error, ℹ️ for info)

### Unit Tests
Tests updated to match new behavior:
- `test_snapshot_discover_no_snapshots`: Expects exit code 0
- `test_snapshot_discover_no_peers_connected`: Expects exit code 1

Note: Full test suite cannot run due to pre-existing CLI structure issues unrelated to this change.

## Related Documentation

- [P2P_SNAPSHOT_CLI_FIX.md](P2P_SNAPSHOT_CLI_FIX.md) - Original P2P snapshot discovery implementation
- [SNAPSHOT_PEER_DISCOVERY_CLI_FIX.md](SNAPSHOT_PEER_DISCOVERY_CLI_FIX.md) - Peer discovery functionality
- [SNAPSHOT_ERROR_MESSAGE_IMPROVEMENTS.md](SNAPSHOT_ERROR_MESSAGE_IMPROVEMENTS.md) - Error messaging improvements
