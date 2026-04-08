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

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$BuildDir = Join-Path $ProjectRoot "build\windows"
$InstallDir = Join-Path $BuildDir "stage"
$BuildType = if ($Debug) { "Debug" } else { "Release" }

function Write-Log {
    param([string]$Message)
    Write-Host "[BUILD] $Message" -ForegroundColor Green
}

if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    throw "CMake not found."
}

if (-not (Get-Command python -ErrorAction SilentlyContinue) -and -not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python 3 is required."
}

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
    & cmake $ProjectRoot `
        -DCMAKE_BUILD_TYPE="$BuildType" `
        -DWALLET_REMOTE_RPC_ONLY=OFF `
        -DBUILD_TESTING=OFF `
        -G "Visual Studio 17 2022" `
        -A x64

    Write-Log "Building wallet"
    & cmake --build . --config $BuildType -j $Jobs

    Write-Log "Installing staged runtime"
    if (Test-Path $InstallDir) {
        Remove-Item -Recurse -Force $InstallDir
    }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    & cmake --install . --config $BuildType --prefix $InstallDir
} finally {
    Pop-Location
}

$PythonCmd = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
& $PythonCmd "$ScriptDir\verify-bundle-layout.py" --platform windows --path $InstallDir

Write-Log ""
Write-Log "Build completed successfully"
Write-Log "  Staged runtime: $InstallDir"
Write-Log "  Smoke test:     .\scripts\smoke-test-windows.ps1 -WalletPath `"$InstallDir`""
