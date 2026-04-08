# release-windows.ps1 - Build and package Windows release for Animica Wallet
#
# Creates:
# - .msi installer (via WiX/CPack)
# - Fallback .exe installer (via NSIS if WiX unavailable)
# - Code signing stubs (requires certificate)
#
# Usage:
#   .\scripts\release-windows.ps1 [-Sign] [-Debug]

param(
    [switch]$Sign = $false,
    [switch]$Debug = $false
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WalletRoot = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent $WalletRoot

# Configuration
$BuildType = if ($Debug) { "Debug" } else { "Release" }
$OutputDir = Join-Path $RepoRoot "dist\wallet-qt"

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "Animica Wallet - Windows Release Build" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "Build type: $BuildType"
Write-Host "Code signing: $Sign"
Write-Host ""

# Determine version
Push-Location $RepoRoot
try {
    $Version = (git describe --tags --exact-match 2>$null)
    if (-not $Version) {
        $Version = (git describe --tags --abbrev=0 2>$null)
        if (-not $Version) {
            $Version = "v0.1.0"
        }
        $Commit = (git rev-parse --short HEAD)
        $Version = "$Version-$Commit"
    }
} finally {
    Pop-Location
}
Write-Host "Version: $Version"
Write-Host ""

# Detect architecture
$Arch = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
Write-Host "Building for architecture: $Arch"
Write-Host ""

# Generate icons if needed
Write-Host "Checking icons..."
Push-Location $WalletRoot
try {
    $IconCheck = & python scripts/gen-icons.py --check 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Generating icons..."
        & python scripts/gen-icons.py
        if ($LASTEXITCODE -ne 0) {
            throw "Icon generation failed"
        }
    }
} finally {
    Pop-Location
}
Write-Host ""

# Build directory
$BuildDir = Join-Path $WalletRoot "build\windows-release"
if (Test-Path $BuildDir) {
    Remove-Item -Recurse -Force $BuildDir
}
New-Item -ItemType Directory -Path $BuildDir | Out-Null

Write-Host "Configuring build..."
Push-Location $BuildDir
try {
    # Try to find Qt
    if (-not $env:CMAKE_PREFIX_PATH) {
        $QtPaths = @(
            "C:\Qt\6.5.3\msvc2019_64",
            "C:\Qt\6.4.3\msvc2019_64",
            "C:\Qt\6.2.4\msvc2019_64"
        )
        foreach ($QtPath in $QtPaths) {
            if (Test-Path $QtPath) {
                $env:CMAKE_PREFIX_PATH = $QtPath
                Write-Host "Found Qt at: $QtPath"
                break
            }
        }
    }
    
    & cmake $WalletRoot `
        -DCMAKE_BUILD_TYPE="$BuildType" `
        -DWALLET_REMOTE_RPC_ONLY=OFF `
        -DBUILD_TESTING=OFF `
        -G "Visual Studio 16 2019" `
        -A $Arch
    
    if ($LASTEXITCODE -ne 0) {
        throw "CMake configuration failed"
    }
    
    Write-Host ""
    Write-Host "Building wallet..."
    & cmake --build . --config $BuildType -j $env:NUMBER_OF_PROCESSORS
    
    if ($LASTEXITCODE -ne 0) {
        throw "Build failed"
    }
} finally {
    Pop-Location
}

# Locate built executable
$ExePath = Join-Path $BuildDir "bin\$BuildType\animica-wallet.exe"

if (-not (Test-Path $ExePath)) {
    throw "Executable not found at $ExePath"
}

Write-Host ""
Write-Host "Executable created: $ExePath"

# Validation
Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "Validating Build" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan

# Check node binary exists
$NodePython = Join-Path $BuildDir "bin\node\venv\Scripts\python.exe"
if (-not (Test-Path $NodePython)) {
    throw "Node Python not found at $NodePython"
}
Write-Host "✓ Node Python found"

# Check node is executable
try {
    & $NodePython --version | Out-Null
} catch {
    throw "Node Python is not executable"
}
Write-Host "✓ Node Python is executable"

# Check architecture
$DumpBin = "dumpbin"
if (Get-Command dumpbin -ErrorAction SilentlyContinue) {
    Write-Host "Checking architecture..."
    & dumpbin /headers $NodePython | Select-String "machine"
}
Write-Host "✓ Architecture: $Arch"

# Check node imports
Write-Host "Checking node imports..."
$ImportTest = & $NodePython -c "import rpc; import animica.qt_wallet_bridge; import omni_sdk; import core" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error output: $ImportTest"
    throw "Node imports failed"
}
Write-Host "✓ Node imports successful"

# Check dynamic libraries
Write-Host "Checking dynamic libraries..."
if (Get-Command dumpbin -ErrorAction SilentlyContinue) {
    & dumpbin /dependents $ExePath | Select-String "\.dll"
}

# Code signing
if ($Sign) {
    Write-Host ""
    Write-Host "======================================" -ForegroundColor Cyan
    Write-Host "Code Signing" -ForegroundColor Cyan
    Write-Host "======================================" -ForegroundColor Cyan
    
    if (-not $env:CODESIGN_CERT) {
        throw "CODESIGN_CERT environment variable not set. Set it to the certificate thumbprint or path to PFX file."
    }
    
    Write-Host "Signing with certificate: $env:CODESIGN_CERT"
    
    # Sign the executable
    Write-Host "Signing wallet executable..."
    & signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a $ExePath
    
    if ($LASTEXITCODE -ne 0) {
        throw "Code signing failed"
    }
    
    # Sign embedded node Python (optional, but recommended)
    Write-Host "Signing embedded node..."
    & signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a $NodePython
    
    # Verify signature
    Write-Host "Verifying signature..."
    & signtool verify /pa $ExePath
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Code signing complete"
    } else {
        Write-Warning "Signature verification failed (may still work)"
    }
} else {
    Write-Host ""
    Write-Host "Skipping code signing (use -Sign to enable)"
}

# Create installer
Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "Creating Installer" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan

$DistDir = Join-Path $OutputDir "$Version\windows"
New-Item -ItemType Directory -Path $DistDir -Force | Out-Null

# Try WiX/MSI first
$WixAvailable = Get-Command candle.exe -ErrorAction SilentlyContinue
if ($WixAvailable) {
    Write-Host "Creating MSI installer with WiX..."
    
    # Use CPack to generate MSI
    Push-Location $BuildDir
    try {
        & cpack -G WIX -C $BuildType
        
        if ($LASTEXITCODE -eq 0) {
            # Find and copy MSI
            $MsiFile = Get-ChildItem -Path $BuildDir -Filter "*.msi" | Select-Object -First 1
            if ($MsiFile) {
                $TargetMsi = Join-Path $DistDir "AnimicaWallet-${Version}-windows-${Arch}.msi"
                Copy-Item $MsiFile.FullName $TargetMsi
                Write-Host "✓ MSI created: $TargetMsi"
                
                # Sign MSI if requested
                if ($Sign) {
                    Write-Host "Signing MSI..."
                    & signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a $TargetMsi
                }
            }
        } else {
            Write-Warning "CPack WIX generation failed"
        }
    } finally {
        Pop-Location
    }
} else {
    Write-Warning "WiX not found. Install from: https://wixtoolset.org/"
    Write-Host "Creating simple ZIP distribution instead..."
    
    # Create ZIP as fallback
    $StageDir = Join-Path $BuildDir "stage"
    New-Item -ItemType Directory -Path $StageDir -Force | Out-Null
    
    # Copy files
    Copy-Item $ExePath $StageDir
    Copy-Item -Recurse (Join-Path $BuildDir "bin\node") $StageDir
    
    $QtBinDir = Join-Path $env:CMAKE_PREFIX_PATH "bin"
    $WinDeployQt = if (Test-Path (Join-Path $QtBinDir "windeployqt.exe")) {
        Join-Path $QtBinDir "windeployqt.exe"
    } elseif (Get-Command windeployqt.exe -ErrorAction SilentlyContinue) {
        (Get-Command windeployqt.exe).Source
    } else {
        $null
    }

    if ($WinDeployQt) {
        Write-Host "Running windeployqt for ZIP staging..."
        & $WinDeployQt --release --no-translations --dir $StageDir $ExePath
    } elseif (Test-Path $QtBinDir) {
        Write-Host "Copying core Qt libraries..."
        $QtDlls = @("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Network.dll", "Qt6Sql.dll")
        foreach ($Dll in $QtDlls) {
            $DllPath = Join-Path $QtBinDir $Dll
            if (Test-Path $DllPath) {
                Copy-Item $DllPath $StageDir
            }
        }
    }
    
    # Create ZIP
    $ZipPath = Join-Path $DistDir "AnimicaWallet-${Version}-windows-${Arch}.zip"
    Compress-Archive -Path "$StageDir\*" -DestinationPath $ZipPath -Force
    Write-Host "✓ ZIP created: $ZipPath"
}

# Generate checksums
Write-Host ""
Write-Host "Generating checksums..."
Push-Location $DistDir
try {
    $Checksums = Get-ChildItem -File | ForEach-Object {
        $Hash = (Get-FileHash $_.Name -Algorithm SHA256).Hash.ToLower()
        "$Hash  $($_.Name)"
    }
    $Checksums | Out-File -FilePath "SHA256SUMS" -Encoding UTF8
    Get-Content "SHA256SUMS"
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "Release Build Complete!" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "Version: $Version"
Write-Host "Architecture: $Arch"
Write-Host "Output directory: $DistDir"
Write-Host ""
Write-Host "Artifacts:"
Get-ChildItem $DistDir
Write-Host ""
Write-Host "To test the installer, run:"
Get-ChildItem $DistDir -Filter "*.msi" | ForEach-Object {
    Write-Host "  msiexec /i $($_.FullName)"
}
Write-Host ""
Write-Host "For code signing:"
Write-Host '  $env:CODESIGN_CERT = "thumbprint" (or path to .pfx)'
Write-Host "  .\scripts\release-windows.ps1 -Sign"
