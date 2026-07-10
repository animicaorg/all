# smoke-test-windows.ps1 - Smoke test for Windows AnimicaWallet
#
# Tests:
# 1. Node binary exists and runs
# 2. Node starts and RPC becomes reachable
# 3. Node responds to status queries
# 4. Node shuts down cleanly
#
# Usage:
#   .\scripts\smoke-test-windows.ps1 <path-to-executable-or-installer-dir>

param(
    [Parameter(Mandatory=$true)]
    [string]$WalletPath
)

$ErrorActionPreference = "Stop"

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "Windows Wallet Smoke Test" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "Wallet: $WalletPath"
Write-Host ""

$PythonCmd = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
& $PythonCmd "$(Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'verify-bundle-layout.py')" --platform windows --path $WalletPath

# Determine if this is an exe or a directory
$IsDirectory = Test-Path -Path $WalletPath -PathType Container

if ($IsDirectory) {
    # Assume it's a build output directory
    $WalletExe = Join-Path $WalletPath "animica-wallet.exe"
    if (-not (Test-Path $WalletExe)) {
        # Try Release subdirectory
        $WalletExe = Join-Path $WalletPath "Release\animica-wallet.exe"
    }
} else {
    # It's an exe file
    $WalletExe = $WalletPath
}

if (-not (Test-Path $WalletExe)) {
    Write-Host "Error: Wallet executable not found: $WalletExe" -ForegroundColor Red
    exit 1
}

Write-Host "Using executable: $WalletExe"
Write-Host ""

# Test 1: Check node binary exists
Write-Host "[1/5] Checking node binary..." -ForegroundColor Yellow

$WalletDir = Split-Path -Parent $WalletExe
# The bundled runtime is a relocatable python-build-standalone tree
# (node\venv\python.exe at the root); older `python -m venv` builds kept it under
# node\venv\Scripts\python.exe. Accept either, next to the exe or one level up.
$NodePython = $null
foreach ($base in @($WalletDir, (Split-Path -Parent $WalletDir))) {
    foreach ($rel in @("node\venv\python.exe", "node\venv\Scripts\python.exe")) {
        $cand = Join-Path $base $rel
        if (Test-Path $cand -PathType Leaf) { $NodePython = $cand; break }
    }
    if ($NodePython) { break }
}

if (-not $NodePython) {
    Write-Host "❌ FAIL: Node Python not found under $WalletDir (node\venv\python.exe or node\venv\Scripts\python.exe)" -ForegroundColor Red
    exit 1
}

Write-Host "✓ Node binary exists" -ForegroundColor Green
Write-Host ""

# Test 2: Check node version and imports
Write-Host "[2/5] Testing node imports..." -ForegroundColor Yellow

try {
    $VersionOutput = & $NodePython --version 2>&1
    Write-Host "Python version: $VersionOutput"
} catch {
    Write-Host "❌ FAIL: Node Python --version failed" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

try {
    $ImportOutput = & $NodePython -c "import sys; import rpc; import animica.qt_wallet_bridge; import animica.wallet_qr; import core; print('All imports OK')" 2>&1
    Write-Host $ImportOutput
} catch {
    Write-Host "❌ FAIL: Node imports failed" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

Write-Host "✓ Node imports successful" -ForegroundColor Green
Write-Host ""

# Test 3: Start node and check RPC
Write-Host "[3/5] Starting node..." -ForegroundColor Yellow

# Use a temporary datadir for testing
$TestDataDir = Join-Path $env:TEMP "animica-smoke-test-$(Get-Random)"
New-Item -ItemType Directory -Path $TestDataDir -Force | Out-Null

# Cleanup function
function Cleanup {
    Write-Host ""
    Write-Host "Cleaning up..."
    
    if ($script:NodeProcess -and -not $script:NodeProcess.HasExited) {
        Write-Host "Stopping node (PID $($script:NodeProcess.Id))..."
        try {
            $script:NodeProcess.Kill()
            $script:NodeProcess.WaitForExit(5000)
        } catch {
            Write-Host "Warning: Failed to stop node process" -ForegroundColor Yellow
        }
    }
    
    if (Test-Path $TestDataDir) {
        Remove-Item -Recurse -Force $TestDataDir -ErrorAction SilentlyContinue
    }
}

# Register cleanup
Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action { Cleanup } | Out-Null

# Start node in background
$RpcPort = 18545  # Use non-standard port to avoid conflicts
$LogFile = Join-Path $TestDataDir "node.log"

$ProcessInfo = New-Object System.Diagnostics.ProcessStartInfo
$ProcessInfo.FileName = $NodePython
$ProcessInfo.Arguments = "-m rpc --host 127.0.0.1 --port $RpcPort --chain-id 1337 --datadir `"$TestDataDir`" --log-level INFO"
$ProcessInfo.RedirectStandardOutput = $true
$ProcessInfo.RedirectStandardError = $true
$ProcessInfo.UseShellExecute = $false
$ProcessInfo.CreateNoWindow = $true

$script:NodeProcess = New-Object System.Diagnostics.Process
$script:NodeProcess.StartInfo = $ProcessInfo

# Capture output to log file
$script:LogOutput = @()
$script:NodeProcess.add_OutputDataReceived({
    if ($EventArgs.Data) {
        $script:LogOutput += $EventArgs.Data
    }
})
$script:NodeProcess.add_ErrorDataReceived({
    if ($EventArgs.Data) {
        $script:LogOutput += $EventArgs.Data
    }
})

$script:NodeProcess.Start() | Out-Null
$script:NodeProcess.BeginOutputReadLine()
$script:NodeProcess.BeginErrorReadLine()

Write-Host "Node started with PID $($script:NodeProcess.Id)"

# Wait for node to become ready
Write-Host "Waiting for node RPC to become ready..."
$MaxWait = 30
$Waited = 0

while ($Waited -lt $MaxWait) {
    try {
        $HealthResponse = Invoke-WebRequest -Uri "http://127.0.0.1:$RpcPort/health" -UseBasicParsing -TimeoutSec 1 -ErrorAction SilentlyContinue
        if ($HealthResponse.StatusCode -eq 200) {
            Write-Host "✓ Node RPC is ready" -ForegroundColor Green
            break
        }
    } catch {
        # Expected during startup
    }
    
    # Check if process is still running
    if ($script:NodeProcess.HasExited) {
        Write-Host "❌ FAIL: Node process died" -ForegroundColor Red
        Write-Host "Last log output:"
        $script:LogOutput | Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
        Cleanup
        exit 1
    }
    
    Start-Sleep -Milliseconds 1000
    $Waited++
    Write-Host -NoNewline "."
}
Write-Host ""

if ($Waited -ge $MaxWait) {
    Write-Host "❌ FAIL: Node RPC did not become ready within ${MaxWait}s" -ForegroundColor Red
    Write-Host "Last log output:"
    $script:LogOutput | Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
    Cleanup
    exit 1
}

Write-Host ""

# Test 4: Query node status
Write-Host "[4/5] Testing node RPC calls..." -ForegroundColor Yellow

# Test /health endpoint
try {
    $HealthResponse = Invoke-WebRequest -Uri "http://127.0.0.1:$RpcPort/health" -UseBasicParsing
    Write-Host "✓ /health: $($HealthResponse.Content)" -ForegroundColor Green
} catch {
    Write-Host "❌ FAIL: /health endpoint failed" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Cleanup
    exit 1
}

# Test /status endpoint
try {
    $StatusResponse = Invoke-WebRequest -Uri "http://127.0.0.1:$RpcPort/status" -UseBasicParsing
    $StatusJson = $StatusResponse.Content | ConvertFrom-Json
    
    if ($StatusJson.chain_id -ne 1337) {
        Write-Host "❌ FAIL: Expected chain_id 1337, got: $($StatusJson.chain_id)" -ForegroundColor Red
        Cleanup
        exit 1
    }
    
    Write-Host "✓ /status: chain_id=$($StatusJson.chain_id)" -ForegroundColor Green
} catch {
    Write-Host "❌ FAIL: /status endpoint failed" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Cleanup
    exit 1
}

Write-Host ""

# Test 5: Clean shutdown
Write-Host "[5/5] Testing clean shutdown..." -ForegroundColor Yellow

try {
    $script:NodeProcess.Kill()
    $script:NodeProcess.WaitForExit(5000)
} catch {
    Write-Host "Warning: Node did not stop gracefully" -ForegroundColor Yellow
}

if (-not $script:NodeProcess.HasExited) {
    Write-Host "❌ FAIL: Node process still running after shutdown" -ForegroundColor Red
    Cleanup
    exit 1
}

$script:NodeProcess = $null  # Prevent cleanup from trying again
Write-Host "✓ Node shutdown successful" -ForegroundColor Green

# Final Qt runtime check
Write-Host ""
Write-Host "[6/6] Checking deployed Qt runtime layout..." -ForegroundColor Yellow
$QtPlatformCandidates = @(
    (Join-Path $WalletDir "plugins\platforms\qwindows.dll"),
    (Join-Path $WalletDir "platforms\qwindows.dll")
)
$QtPlatform = $QtPlatformCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $QtPlatform) {
    Write-Host "❌ FAIL: qwindows.dll not found in plugins\\platforms or platforms under $WalletDir" -ForegroundColor Red
    Cleanup
    exit 1
}

$QtConf = Join-Path $WalletDir "qt.conf"
if (-not (Test-Path $QtConf)) {
    Write-Host "❌ FAIL: qt.conf not found at $QtConf" -ForegroundColor Red
    Cleanup
    exit 1
}
Write-Host "✓ qwindows.dll present: $QtPlatform" -ForegroundColor Green
Write-Host "✓ qt.conf present" -ForegroundColor Green

# Final cleanup
Cleanup

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "✅ All smoke tests passed!" -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "The wallet is ready for distribution."
