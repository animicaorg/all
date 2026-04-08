# build-windows.ps1 - Cross-platform deterministic build script for Animica Wallet (Windows)
#
# This script builds the Animica Qt wallet and bundles the node for Windows.
# It performs strict prerequisite checking and provides actionable error messages.
#
# Usage:
#   .\build-windows.ps1 [OPTIONS]
#
# Options:
#   -Debug          Build in Debug mode (default: Release)
#   -Clean          Clean build directory before building
#   -QtPath <path>  Override Qt installation path
#   -Jobs <n>       Number of parallel build jobs (default: auto-detect)
#   -Help           Show this help message

param(
    [switch]$Debug,
    [switch]$Clean,
    [string]$QtPath = "",
    [int]$Jobs = 0,
    [switch]$Help
)

# Strict error handling
$ErrorActionPreference = 'Stop'
$PSDefaultParameterValues['*:ErrorAction'] = 'Stop'

# Script location
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent $ProjectRoot

# Build configuration
$BuildType = if ($Debug) { "Debug" } else { "Release" }

# Colors for output
function Write-Log {
    param([string]$Message)
    Write-Host "[BUILD] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Exit-WithError {
    param([string]$Message)
    Write-Error-Custom $Message
    exit 1
}

function Show-Help {
    Get-Help $MyInvocation.MyCommand.Path
    exit 0
}

if ($Help) {
    Show-Help
}

Write-Log "=========================================="
Write-Log "Animica Wallet Build Script (Windows)"
Write-Log "=========================================="
Write-Log "Build type: $BuildType"
Write-Log "Project root: $ProjectRoot"
Write-Log ""

# ==================== Prerequisite Checks ====================

Write-Log "Checking prerequisites..."

# Check CMake
if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    Exit-WithError "CMake not found. Install from: https://cmake.org/download/"
}

$CmakeVersion = (cmake --version | Select-String -Pattern '\d+\.\d+\.\d+').Matches[0].Value
Write-Log "✓ CMake $CmakeVersion found"

$CmakeVersionParts = $CmakeVersion.Split('.')
$CmakeMajor = [int]$CmakeVersionParts[0]
$CmakeMinor = [int]$CmakeVersionParts[1]

if ($CmakeMajor -lt 3 -or ($CmakeMajor -eq 3 -and $CmakeMinor -lt 16)) {
    Exit-WithError "CMake 3.16+ required (found $CmakeVersion)"
}

# Check for C++ compiler (Visual Studio or MinGW)
$VsInstalled = $false
$MingwInstalled = $false

# Check for Visual Studio
$VsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $VsWhere) {
    $VsPath = & $VsWhere -latest -property installationPath -format value
    if ($VsPath) {
        $VsInstalled = $true
        Write-Log "✓ Visual Studio found at: $VsPath"
    }
}

# Check for MinGW
if (Get-Command g++ -ErrorAction SilentlyContinue) {
    $MingwInstalled = $true
    $GccVersion = (g++ --version | Select-Object -First 1)
    Write-Log "✓ MinGW found: $GccVersion"
}

if (-not $VsInstalled -and -not $MingwInstalled) {
    Write-Error-Custom "No C++ compiler found."
    Write-Error-Custom ""
    Write-Error-Custom "Install one of:"
    Write-Error-Custom "  Visual Studio 2019+: https://visualstudio.microsoft.com/downloads/"
    Write-Error-Custom "  MinGW-w64: https://www.mingw-w64.org/"
    Exit-WithError "C++ compiler not found"
}

# Check Qt
Write-Log "Checking Qt installation..."

if ($QtPath -ne "") {
    # User provided Qt path
    if (-not (Test-Path $QtPath)) {
        Exit-WithError "Qt path does not exist: $QtPath"
    }
    $env:CMAKE_PREFIX_PATH = $QtPath
    Write-Log "✓ Using Qt from: $QtPath"
} else {
    # Try to find Qt automatically
    $QtFound = $false
    
    # Common Qt installation paths
    $QtSearchPaths = @(
        "C:\Qt\6.*\msvc*",
        "C:\Qt\6.*\mingw*",
        "C:\Qt6\*",
        "$env:USERPROFILE\Qt\6.*\msvc*",
        "$env:USERPROFILE\Qt\6.*\mingw*"
    )
    
    foreach ($pattern in $QtSearchPaths) {
        $paths = Get-ChildItem -Path $pattern -Directory -ErrorAction SilentlyContinue | Sort-Object -Descending
        if ($paths) {
            $QtPath = $paths[0].FullName
            if (Test-Path "$QtPath\bin\qmake.exe") {
                $env:CMAKE_PREFIX_PATH = $QtPath
                $QtVersion = & "$QtPath\bin\qmake.exe" -query QT_VERSION
                $QtFound = $true
                Write-Log "✓ Qt $QtVersion found at: $QtPath"
                break
            }
        }
    }
    
    if (-not $QtFound) {
        Write-Error-Custom "Qt 6 not found."
        Write-Error-Custom ""
        Write-Error-Custom "Install Qt from: https://www.qt.io/download-open-source"
        Write-Error-Custom ""
        Write-Error-Custom "If Qt is installed in a custom location, use: -QtPath C:\path\to\qt"
        Exit-WithError "Qt not found"
    }
}

# Check Python
Write-Log "Checking Python installation..."

$Python = $null
$PythonCandidates = @("python", "python3", "py")

foreach ($py in $PythonCandidates) {
    if (Get-Command $py -ErrorAction SilentlyContinue) {
        try {
            $PyVersion = & $py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($PyVersion) {
                $VersionParts = $PyVersion.Split('.')
                $PyMajor = [int]$VersionParts[0]
                $PyMinor = [int]$VersionParts[1]
                
                if ($PyMajor -eq 3 -and $PyMinor -ge 10) {
                    $Python = $py
                    Write-Log "✓ Python $PyVersion found"
                    break
                }
            }
        } catch {
            continue
        }
    }
}

if (-not $Python) {
    Write-Error-Custom "Python 3.10+ not found."
    Write-Error-Custom ""
    Write-Error-Custom "Install Python from: https://www.python.org/downloads/"
    Write-Error-Custom "Make sure to check 'Add Python to PATH' during installation"
    Exit-WithError "Python not found"
}

# Check if venv module is available
try {
    & $Python -m venv --help > $null 2>&1
} catch {
    Write-Error-Custom "Python venv module not available."
    Write-Error-Custom ""
    Write-Error-Custom "Reinstall Python with venv support"
    Exit-WithError "Python venv module not found"
}

# Determine number of jobs
if ($Jobs -eq 0) {
    $Jobs = $env:NUMBER_OF_PROCESSORS
    Write-Log "Auto-detected $Jobs CPU cores for parallel build"
} else {
    Write-Log "Using $Jobs parallel jobs"
}

Write-Log "✓ All prerequisites satisfied"
Write-Log ""

# ==================== Build ====================

$BuildDir = Join-Path $ProjectRoot "build\windows"

if ($Clean) {
    Write-Log "Cleaning build directory..."
    if (Test-Path $BuildDir) {
        Remove-Item -Recurse -Force $BuildDir
    }
}

Write-Log "Creating build directory..."
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

Write-Log "Configuring CMake..."
Push-Location $BuildDir

try {
    if ($VsInstalled) {
        # Use Visual Studio generator
        cmake $ProjectRoot `
            -DCMAKE_BUILD_TYPE="$BuildType" `
            -DWALLET_REMOTE_RPC_ONLY=OFF `
            -G "Visual Studio 16 2019"
        
        if ($LASTEXITCODE -ne 0) {
            # Try newer VS version
            cmake $ProjectRoot `
                -DCMAKE_BUILD_TYPE="$BuildType" `
                -DWALLET_REMOTE_RPC_ONLY=OFF `
                -G "Visual Studio 17 2022"
        }
    } else {
        # Use MinGW
        cmake $ProjectRoot `
            -DCMAKE_BUILD_TYPE="$BuildType" `
            -DWALLET_REMOTE_RPC_ONLY=OFF `
            -G "MinGW Makefiles"
    }
    
    if ($LASTEXITCODE -ne 0) {
        Exit-WithError "CMake configuration failed"
    }
    
    Write-Log "Building wallet and node..."
    cmake --build . --config $BuildType -j $Jobs
    
    if ($LASTEXITCODE -ne 0) {
        Exit-WithError "Build failed"
    }
    
} finally {
    Pop-Location
}

Write-Log ""
Write-Log "=========================================="
Write-Log "Build completed successfully!"
Write-Log "=========================================="
Write-Log ""
Write-Log "Artifacts:"
Write-Log "  Wallet executable: $BuildDir\bin\$BuildType\animica-wallet.exe"
Write-Log "  Bundled node:      $BuildDir\bin\node\"
Write-Log ""
Write-Log "To run the wallet:"
Write-Log "  $BuildDir\bin\$BuildType\animica-wallet.exe"
Write-Log ""
Write-Log "To create a distribution package:"
Write-Log "  mkdir $ProjectRoot\dist\windows"
Write-Log "  Copy-Item -Recurse $BuildDir\bin\* $ProjectRoot\dist\windows\"
Write-Log ""
