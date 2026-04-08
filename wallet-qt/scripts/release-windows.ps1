# release-windows.ps1 - Build and package a native Windows release for Animica Wallet
#
# Usage:
#   .\scripts\release-windows.ps1 [-Sign] [-Debug] [-PerMachine]

param(
    [switch]$Sign = $false,
    [switch]$Debug = $false,
    [switch]$PerMachine = $false
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WalletRoot = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent $WalletRoot
$BuildType = if ($Debug) { "Debug" } else { "Release" }
$BuildDir = Join-Path $WalletRoot "build\windows-release"
$InstallDir = Join-Path $BuildDir "stage"
$OutputDir = Join-Path $RepoRoot "dist\wallet-qt"
$Arch = "x64"
$InstallScope = if ($PerMachine) { "perMachine" } else { "perUser" }

function Write-Log {
    param([string]$Message)
    Write-Host "[RELEASE] $Message" -ForegroundColor Cyan
}

function Find-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) { return "py" }
    if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }
    throw "Python 3 is required."
}

function Find-WinDeployQt {
    if ($env:CMAKE_PREFIX_PATH) {
        $candidate = Join-Path $env:CMAKE_PREFIX_PATH "bin\windeployqt.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    $command = Get-Command windeployqt.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Sign-File {
    param([string]$Path)
    if (-not $Sign) { return }
    if (-not $env:CODESIGN_CERT) {
        throw "CODESIGN_CERT must point to a certificate thumbprint or PFX file when -Sign is used."
    }
    & signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a $Path
    if ($LASTEXITCODE -ne 0) {
        throw "Code signing failed for $Path"
    }
}

Push-Location $RepoRoot
try {
    $Version = (git describe --tags --exact-match 2>$null)
    if (-not $Version) {
        $BaseVersion = (git describe --tags --abbrev=0 2>$null)
        if (-not $BaseVersion) {
            $BaseVersion = "v0.1.0"
        }
        $Version = "$BaseVersion-$(git rev-parse --short HEAD)"
    }
} finally {
    Pop-Location
}

Write-Log "======================================"
Write-Log "Animica Wallet - Windows Release Build"
Write-Log "======================================"
Write-Log "Build type: $BuildType"
Write-Log "Version: $Version"
Write-Log "Architecture: $Arch"
Write-Log "Install scope: $(if ($PerMachine) { "per-machine" } else { "per-user" })"
Write-Log ""

$PythonCmd = Find-PythonCommand

Push-Location $WalletRoot
try {
    & $PythonCmd scripts\gen-icons.py --check 2>$null
    if ($LASTEXITCODE -ne 0) {
        & $PythonCmd scripts\gen-icons.py
    }
} finally {
    Pop-Location
}

if (Test-Path $BuildDir) {
    Remove-Item -Recurse -Force $BuildDir
}
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

if (-not $env:CMAKE_PREFIX_PATH) {
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

Push-Location $BuildDir
try {
    & cmake $WalletRoot `
        -DCMAKE_BUILD_TYPE="$BuildType" `
        -DWALLET_REMOTE_RPC_ONLY=OFF `
        -DBUILD_TESTING=OFF `
        -DCPACK_WIX_INSTALL_SCOPE="$InstallScope" `
        -G "Visual Studio 17 2022" `
        -A x64
    if ($LASTEXITCODE -ne 0) {
        throw "CMake configuration failed"
    }

    & cmake --build . --config $BuildType -j [Environment]::ProcessorCount
    if ($LASTEXITCODE -ne 0) {
        throw "Build failed"
    }

    if (Test-Path $InstallDir) {
        Remove-Item -Recurse -Force $InstallDir
    }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    & cmake --install . --config $BuildType --prefix $InstallDir
    if ($LASTEXITCODE -ne 0) {
        throw "Install staging failed"
    }
} finally {
    Pop-Location
}

$WinDeployQt = Find-WinDeployQt
if (-not (Test-Path (Join-Path $InstallDir "platforms\qwindows.dll")) -and $WinDeployQt) {
    Write-Log "Running windeployqt fallback against staged install"
    & $WinDeployQt --release --no-translations --dir $InstallDir (Join-Path $InstallDir "animica-wallet.exe")
}

& $PythonCmd "$ScriptDir\verify-bundle-layout.py" --platform windows --path $InstallDir

Sign-File (Join-Path $InstallDir "animica-wallet.exe")
Sign-File (Join-Path $InstallDir "node\venv\Scripts\python.exe")

$DistDir = Join-Path $OutputDir "$Version\windows"
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

$ZipPath = Join-Path $DistDir "AnimicaWallet-${Version}-windows-${Arch}.zip"
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path (Join-Path $InstallDir "*") -DestinationPath $ZipPath -Force

$WixAvailable = (Get-Command candle.exe -ErrorAction SilentlyContinue) -and (Get-Command light.exe -ErrorAction SilentlyContinue)
if ($WixAvailable) {
    Push-Location $BuildDir
    try {
        & cpack -G WIX -C $BuildType
        if ($LASTEXITCODE -eq 0) {
            $MsiFile = Get-ChildItem -Path $BuildDir -Filter "*.msi" | Select-Object -First 1
            if ($MsiFile) {
                $TargetMsi = Join-Path $DistDir "AnimicaWallet-${Version}-windows-${Arch}.msi"
                Copy-Item $MsiFile.FullName $TargetMsi -Force
                Sign-File $TargetMsi
            }
        } else {
            Write-Warning "CPack WIX generation failed; ZIP artifact is still available."
        }
    } finally {
        Pop-Location
    }
} else {
    Write-Warning "WiX Toolset v3 (candle.exe/light.exe) was not found. MSI generation skipped."
}

Push-Location $DistDir
try {
    Get-ChildItem -File | ForEach-Object {
        $Hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
        "$Hash  $($_.Name)"
    } | Out-File -FilePath "SHA256SUMS" -Encoding UTF8
} finally {
    Pop-Location
}

Write-Log ""
Write-Log "======================================"
Write-Log "Release Build Complete"
Write-Log "======================================"
Write-Log "Staged runtime: $InstallDir"
Write-Log "ZIP artifact:   $ZipPath"
if (Test-Path (Join-Path $DistDir "AnimicaWallet-${Version}-windows-${Arch}.msi")) {
    Write-Log "MSI artifact:   $(Join-Path $DistDir "AnimicaWallet-${Version}-windows-${Arch}.msi")"
}
Write-Log "Smoke test:     .\scripts\smoke-test-windows.ps1 -WalletPath `"$InstallDir`""
Write-Log "Install scope:  $(if ($PerMachine) { "per-machine" } else { "per-user" })"
