# Flutter Wallet Miner - Usage & Testing Guide

## Quick Start

### Prerequisites

1. **Flutter SDK** (≥ 3.24.0)
   ```bash
   flutter --version
   ```

2. **Dart SDK** (≥ 3.5.0) - comes with Flutter

3. **Platform-specific tools**:
   - **Android**: Android Studio + Android SDK
   - **iOS/macOS**: Xcode + CocoaPods
   - **Windows**: Visual Studio with C++ tools
   - **Linux**: GTK3 development packages

### Installation

```bash
cd apps/miner-wallet-flutter

# Install dependencies
flutter pub get

# Verify installation
flutter doctor
```

### Running the App

```bash
# List available devices
flutter devices

# Run on current device (auto-detects)
flutter run

# Run on specific device
flutter run -d macos        # macOS
flutter run -d chrome       # Web browser
flutter run -d windows      # Windows
flutter run -d <device-id>  # Specific device
```

## First Launch

### Using the Setup Wizard

1. **Launch the app** - it will detect if no configuration exists
2. **Navigate to** `/wizard` if not automatically shown
3. **Complete the 4-step wizard**:

   **Step 1: Network Configuration**
   - RPC URL: `https://rpc.clearblocker.com` (or your local node)
   - Chain ID: `2` (mainnet) or your network's ID

   **Step 2: Wallet Setup**
   - Enter your Animica wallet address (starts with `anim1`)
   - This is where mining rewards will be sent

   **Step 3: Mining Settings**
   - Enable/disable CPU mining
   - Set thread count (slider)
   - GPU settings (if available)

   **Step 4: Review & Complete**
   - Review all settings
   - Click "Finish Setup"
   - Redirects to Dashboard

### Manual Configuration

Alternatively, skip the wizard and configure via Settings:

```
Settings → JSON Configuration
- Edit the raw config JSON
- Click "Validate" to check syntax
- Click "Save & Apply"
```

## Using the Application

### Dashboard

**View chain status:**
```
Dashboard shows:
- Chain ID (from RPC)
- Block Height (current)
- Sync Status (syncing % or synced)
```

**Start mining:**
1. Click "Start Mining" button
2. Status changes to "Starting..."
3. When mining: Status = "Mining", Hashrate updates
4. Click "Stop" to halt mining

**Refresh data:**
- Click refresh icon in app bar
- Invalidates chain data caches
- Fetches fresh data from RPC

### Devices Page

**View detected hardware:**
```
Devices → Shows:
- CPU (cores, threads)
- GPU(s) (memory, compute units)
```

**Enable/disable devices:**
1. Toggle switch on device card
2. Configuration saves automatically

**Configure devices:**
1. Click ⚙️ (gear icon) on device card
2. Adjust settings:
   - **CPU**: Thread count (slider)
   - **GPU**: Intensity (1-10 scale)
3. Close dialog (saves automatically)

**Refresh device list:**
- Click refresh icon in app bar
- Re-detects all hardware

### Pools Page

**Enable pool mining:**
1. Toggle "Pool Mining" switch ON
2. Pool configuration form appears

**Configure pool:**
1. Enter pool URL: `stratum+tcp://pool.example.com:3333`
2. Enter username/wallet address
3. Click "Save Pool Settings"

**Use popular pools:**
- Click [+] next to pool name
- URL auto-fills in the form
- Enter username and save

**Disable pool mining:**
1. Toggle "Pool Mining" switch OFF
2. Switches to solo mining mode

### Logs Page

**View logs:**
- Real-time log streaming
- Auto-scrolls to bottom
- Error lines highlighted in red

**Copy logs:**
- Click 📋 (copy icon)
- All logs copied to clipboard

**Clear logs:**
- Click 🗑️ (trash icon)
- Clears all log entries

**Manual scroll:**
- Scroll up to view older logs
- Auto-scroll pauses
- Click ⬇️ to resume auto-scroll

### Stats Page

**View statistics:**
- Current hashrate (real-time)
- Blocks found (session total)
- Shares found (session total)
- Average hashrate (session average)

**View hashrate chart:**
- Line chart shows last 60 data points
- Updates in real-time
- Scroll/zoom on desktop

**Reset chart:**
- Click refresh icon
- Clears all chart data
- Starts fresh collection

**Session stats:**
- Data points collected
- Peak hashrate
- Minimum hashrate

### Config Editor Page

**Edit configuration:**
1. JSON editor with syntax highlighting
2. Edit any configuration value
3. Click "Validate" to check syntax
4. Click "Save & Apply" to save

**Reset to defaults:**
1. Click reset icon
2. Confirm dialog
3. Resets to default configuration

**Configuration structure:**
```json
{
  "network": { ... },   // RPC & chain settings
  "miner": { ... },     // Mining config
  "cpu": { ... },       // CPU settings
  "gpus": [ ... ],      // GPU settings
  "pool": { ... },      // Pool config (or null)
  "ui": { ... }         // UI preferences
}
```

### Settings Page

**Navigate to pages:**
- Devices → Device configuration
- Pool Settings → Pool config
- View Logs → Log viewer
- Statistics → Stats & charts
- JSON Configuration → Config editor

**Toggle preferences:**
- System Tray → Minimize to tray (desktop)
- Notifications → Enable/disable notifications

**About:**
- Click "About" to view version info

### Wallet Page

**View wallet info:**
- Address (click to copy)
- Balance (in ANM)
- Nonce (transaction count)

**Refresh balance:**
- Click refresh button
- Fetches latest balance from RPC

**Send transaction:**
1. Enter recipient address
2. Enter amount (ANM)
3. Click "Send"
4. *Note: Transaction signing not yet implemented*

## Testing Features

### Test RPC Connection

```dart
// Dashboard page should show:
Chain ID: 2 (or your network's ID)
Block Height: <current height>
Sync Status: Synced (or syncing %)
```

If errors appear:
- Check RPC URL in Settings → JSON Configuration
- Verify network connectivity
- Check RPC node is running

### Test Device Detection

```bash
# Navigate to Devices page
# Should detect:
- CPU with correct core count
- GPUs if available

# Platform-specific:
- macOS: Uses sysctl, system_profiler
- Linux: Uses lspci, nproc
- Windows: Uses wmic
- Android/iOS: Device-specific APIs
```

If devices not detected:
- Check permissions (especially GPU access)
- Verify platform tools available (lspci, etc.)
- Check logs for detection errors

### Test Mining Process

**Prerequisites:**
```bash
# Ensure animica-miner executable is available:
- In PATH
- In ../mining/animica-miner
- In /usr/local/bin/animica-miner
- Set ANIMICA_MINER_PATH env variable
```

**Start mining test:**
1. Navigate to Dashboard
2. Click "Start Mining"
3. Observe status changes:
   - Starting...
   - Mining
4. Watch for:
   - Hashrate updates
   - Log entries
   - Stats updates

**Stop mining test:**
1. Click "Stop" button
2. Status changes to "Stopping..."
3. Then "Stopped"
4. Hashrate goes to 0

### Test Log Viewer

**Generate logs:**
1. Start mining → generates logs
2. Stop mining → generates logs
3. Errors → highlighted in red

**Test features:**
- Auto-scroll → scroll down stays at bottom
- Manual scroll → scroll up shows ⬇️ button
- Copy logs → clipboard has all logs
- Clear logs → removes all entries

### Test Stats Chart

**Generate data:**
1. Start mining
2. Wait for hashrate updates
3. Chart populates automatically

**Test features:**
- Data points add to chart
- Rolling window (max 60 points)
- Peak/min tracking
- Chart reset

### Test Configuration

**Save config test:**
1. Modify any setting (CPU threads, pool, etc.)
2. Navigate away and back
3. Setting should persist

**JSON editor test:**
1. Settings → JSON Configuration
2. Edit JSON (change a value)
3. Click "Validate" → should pass
4. Click "Save & Apply"
5. Navigate to relevant page
6. Verify change applied

**Reset test:**
1. Settings → JSON Configuration
2. Click reset icon
3. Confirm
4. Verify defaults restored

## Troubleshooting

### App won't start

```bash
# Check Flutter installation
flutter doctor

# Update dependencies
flutter pub get

# Clean and rebuild
flutter clean
flutter pub get
flutter run
```

### RPC connection fails

**Symptoms:**
- Chain ID shows "Error"
- Block height shows "Error"
- Sync status shows "Unknown"

**Solutions:**
1. Verify RPC URL is correct
2. Check network connectivity
3. Try different RPC endpoint
4. Check CORS settings (for web)

### Mining won't start

**Symptoms:**
- Status stuck on "Starting..."
- Error in logs: "Miner executable not found"

**Solutions:**
1. Install animica-miner executable
2. Add to PATH or specific location
3. Set ANIMICA_MINER_PATH environment variable
4. Check file permissions (executable bit)

### Device detection fails

**Symptoms:**
- "No devices detected"
- Only CPU detected (no GPU)

**Solutions:**
1. Check permissions for hardware access
2. Install platform tools (lspci, wmic, etc.)
3. Restart app
4. Check logs for specific errors

### Logs not appearing

**Symptoms:**
- Log viewer is empty
- Mining running but no logs

**Solutions:**
1. Check miner process is running
2. Verify log level in UI config
3. Check stderr/stdout pipes
4. Restart mining

### Stats chart not updating

**Symptoms:**
- Chart remains empty
- No data points added

**Solutions:**
1. Verify mining is running
2. Check hashrate events are firing
3. Reset chart and restart mining
4. Check for console errors

## Development Tips

### Hot Reload

```bash
# While app is running:
# - Press 'r' for hot reload (UI changes)
# - Press 'R' for hot restart (code changes)
# - Press 'q' to quit
```

### Debugging

```bash
# Run with verbose logging
flutter run --verbose

# Run with debug mode
flutter run --debug

# Run with observatory (debugger)
flutter run --start-paused
```

### Code Changes

**After modifying:**
- Models → Full restart required
- Services → Full restart required
- State providers → Full restart required
- UI widgets → Hot reload works
- Theme → Hot reload works

### Testing State

```dart
// Access providers in debug:
ref.read(configProvider);        // Current config
ref.read(miningStatusProvider);  // Mining status
ref.read(hashrateProvider);      // Current hashrate
ref.invalidate(chainIdProvider); // Force refetch
```

## Platform-Specific Notes

### macOS

- **Permissions**: May need to allow app in Security & Privacy
- **Code signing**: Required for distribution
- **System tray**: Supported with system_tray package

### Windows

- **UAC**: May need admin for GPU access
- **Antivirus**: May flag miner executable
- **System tray**: Supported with system_tray package

### Linux

- **Dependencies**: Install GTK3 dev packages
- **GPU detection**: Requires lspci tool
- **Permissions**: May need sudo for hardware access

### Android

- **Permissions**: Declare in AndroidManifest.xml
- **Navigation**: Bottom nav instead of rail
- **Performance**: May throttle background mining

### iOS

- **Permissions**: Declare in Info.plist
- **Background**: Limited background execution
- **Code signing**: Requires Apple Developer account

### Web

- **CORS**: RPC server must allow web origins
- **WebAssembly**: Used for crypto operations
- **Limitations**: No system tray, limited file access
- **Performance**: Slower than native

## Next Steps

1. **Test thoroughly** on your target platforms
2. **Report issues** with detailed logs
3. **Contribute** bug fixes and features
4. **Deploy** to production when ready

## Support

- **Issues**: https://github.com/animicaorg/all/issues
- **Docs**: https://docs.animica.org
- **Community**: Discord / Telegram

---

**Created**: January 6, 2025  
**Version**: 0.1.0+1  
**Phase**: Phase 2 Testing & Usage
