# build-windows.ps1 - Native Windows staged build for Animica Wallet
#
# Produces a self-contained staged runtime under build\windows\stage.
#
# Usage:
#   .\scripts\build-windows.ps1 [-Debug] [-Clean] [-QtPath <path>] [-Jobs <n>]

param(
    [switch]$Debug,
    [switch]$Clean,
    [string]$QtPath = "",
    [int]$Jobs = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $true
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$BuildDir = Join-Path $ProjectRoot "build\windows"
$InstallDir = Join-Path $BuildDir "stage"
$BuildType = if ($Debug) { "Debug" } else { "Release" }

function Write-Log {
    param([string]$Message)
    Write-Host "[BUILD] $Message" -ForegroundColor Green
}

function Find-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return "py"
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        return "python"
    }

    throw "Python 3 is required."
}

function Invoke-ExternalCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$ArgumentList = @(),
        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit code $LASTEXITCODE)"
    }
}

if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    throw "CMake not found."
}

$PythonCmd = Find-PythonCommand

if ($QtPath) {
    $env:CMAKE_PREFIX_PATH = $QtPath
} elseif (-not $env:CMAKE_PREFIX_PATH) {
    foreach ($candidate in @(
        "C:\Qt\6.7.0\msvc2022_64",
        "C:\Qt\6.6.3\msvc2022_64",
        "C:\Qt\6.5.3\msvc2019_64",
        "$env:USERPROFILE\Qt\6.7.0\msvc2022_64",
        "$env:USERPROFILE\Qt\6.6.3\msvc2022_64",
        "$env:USERPROFILE\Qt\6.5.3\msvc2019_64"
    )) {
        if (Test-Path $candidate) {
            $env:CMAKE_PREFIX_PATH = $candidate
            break
        }
    }
}

if ($Clean -and (Test-Path $BuildDir)) {
    Remove-Item -Recurse -Force $BuildDir
}

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

if ($Jobs -le 0) {
    $Jobs = [Environment]::ProcessorCount
}

Push-Location $BuildDir
try {
    Write-Log "Configuring Windows build in $BuildDir"
    Invoke-ExternalCommand `
        -FilePath "cmake" `
        -ArgumentList @(
            $ProjectRoot,
            "-DCMAKE_BUILD_TYPE=$BuildType",
            "-DWALLET_BUNDLE_PYTHON_RUNTIME=ON",
            "-DBUILD_TESTING=OFF",
            "-G", "Visual Studio 17 2022",
            "-A", "x64"
        ) `
        -FailureMessage "CMake configuration failed"

    Write-Log "Building wallet"
    Invoke-ExternalCommand `
        -FilePath "cmake" `
        -ArgumentList @("--build", ".", "--config", $BuildType, "-j", $Jobs) `
        -FailureMessage "Build failed"

    Write-Log "Installing staged runtime"
    if (Test-Path $InstallDir) {
        Remove-Item -Recurse -Force $InstallDir
    }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Invoke-ExternalCommand `
        -FilePath "cmake" `
        -ArgumentList @("--install", ".", "--config", $BuildType, "--prefix", $InstallDir) `
        -FailureMessage "Install staging failed"
} finally {
    Pop-Location
}

# Bundle the OpenSSL 3 runtime DLLs next to animica-wallet.exe.
#
# Qt6 Network links libcrypto-3-x64.dll / libssl-3-x64.dll, but windeployqt does
# not deploy OpenSSL. Without these the app aborts on launch with
# "libcrypto-3-x64.dll not found". Source them from the bundled
# python-build-standalone runtime (node/venv/DLLs), which ships a matching
# OpenSSL 3 pair — self-contained, no dependency on the runner's system OpenSSL.
# System locations are kept only as fallbacks.
Write-Log "Bundling OpenSSL runtime DLLs"
$OpenSslNames = @("libcrypto-3-x64.dll", "libssl-3-x64.dll")
$OpenSslSearchDirs = @(
    (Join-Path $InstallDir "node\venv\DLLs"),
    (Join-Path $InstallDir "node\venv")
)
if ($env:OPENSSL_ROOT_DIR) {
    $OpenSslSearchDirs += (Join-Path $env:OPENSSL_ROOT_DIR "bin")
    $OpenSslSearchDirs += $env:OPENSSL_ROOT_DIR
}
$OpenSslSearchDirs += @(
    "C:\Program Files\OpenSSL-Win64\bin",
    "C:\Program Files\OpenSSL\bin",
    "C:\Strawberry\c\bin"
)
foreach ($dll in $OpenSslNames) {
    $dest = Join-Path $InstallDir $dll
    if (Test-Path $dest -PathType Leaf) {
        Write-Log "  $dll already present at stage root"
        continue
    }
    $found = $null
    foreach ($dir in $OpenSslSearchDirs) {
        $cand = Join-Path $dir $dll
        if (Test-Path $cand -PathType Leaf) { $found = $cand; break }
    }
    if (-not $found) {
        $cmd = Get-Command $dll -ErrorAction SilentlyContinue
        if ($cmd) { $found = $cmd.Source }
    }
    if (-not $found) {
        throw "Required OpenSSL runtime DLL not found: $dll (searched: $($OpenSslSearchDirs -join '; '))"
    }
    Copy-Item -Path $found -Destination $dest -Force
    Write-Log "  bundled $dll from $found"
}

Invoke-ExternalCommand `
    -FilePath $PythonCmd `
    -ArgumentList @((Join-Path $ScriptDir "verify-bundle-layout.py"), "--platform", "windows", "--path", $InstallDir) `
    -FailureMessage "Bundle layout verification failed"

Write-Log ""
Write-Log "Build completed successfully"
Write-Log "  Staged runtime: $InstallDir"
Write-Log "  Smoke test:     .\scripts\smoke-test-windows.ps1 -WalletPath `"$InstallDir`""
