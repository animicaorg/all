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
$ExeInstallerName = "animica-wallet-setup-x64.exe"
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

function Find-MakeNsis {
    foreach ($commandName in @("makensis.exe", "makensis")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command) { return $command.Source }
    }

    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles "NSIS\makensis.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "NSIS\makensis.exe")
    )) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    throw "NSIS (makensis.exe) is required to produce the Windows installer .exe."
}

function Get-VersionDotted {
    param([string]$VersionLabel)

    $trimmed = $VersionLabel.TrimStart('v')
    $core = $trimmed.Split('-')[0]
    $parts = @($core.Split('.'))
    while ($parts.Count -lt 3) {
        $parts += "0"
    }

    return "{0}.{1}.{2}.0" -f $parts[0], $parts[1], $parts[2]
}

function Get-RelativeStagePath {
    param(
        [string]$RootPath,
        [string]$FullPath
    )

    $normalizedRoot = [System.IO.Path]::GetFullPath($RootPath).TrimEnd('\')
    $normalizedFull = [System.IO.Path]::GetFullPath($FullPath)
    return $normalizedFull.Substring($normalizedRoot.Length).TrimStart('\')
}

function New-NsisFileLists {
    param(
        [string]$StageRoot,
        [string]$InstallIncludePath,
        [string]$UninstallIncludePath
    )

    $files = Get-ChildItem -Path $StageRoot -Recurse -File |
        Where-Object { $_.Name -ne "uninstall.exe" } |
        Sort-Object FullName

    $installLines = New-Object System.Collections.Generic.List[string]
    $uninstallLines = New-Object System.Collections.Generic.List[string]
    $lastDir = $null

    foreach ($file in $files) {
        $relativePath = Get-RelativeStagePath -RootPath $StageRoot -FullPath $file.FullName
        $relativeDir = Split-Path $relativePath -Parent
        if ([string]::IsNullOrEmpty($relativeDir)) {
            $relativeDir = "."
        }

        if ($relativeDir -ne $lastDir) {
            if ($relativeDir -eq ".") {
                $installLines.Add('  SetOutPath "$INSTDIR"')
            } else {
                $installLines.Add(("  SetOutPath `"$INSTDIR\{0}`"" -f $relativeDir))
            }
            $lastDir = $relativeDir
        }

        $installLines.Add(("  File `"{0}`"" -f $file.FullName))
    }

    $filesDescending = Get-ChildItem -Path $StageRoot -Recurse -File |
        Where-Object { $_.Name -ne "uninstall.exe" } |
        Sort-Object FullName -Descending
    foreach ($file in $filesDescending) {
        $relativePath = Get-RelativeStagePath -RootPath $StageRoot -FullPath $file.FullName
        $uninstallLines.Add(("  Delete `"$INSTDIR\{0}`"" -f $relativePath))
    }

    $dirsDescending = Get-ChildItem -Path $StageRoot -Recurse -Directory |
        Sort-Object FullName -Descending
    foreach ($directory in $dirsDescending) {
        $relativeDir = Get-RelativeStagePath -RootPath $StageRoot -FullPath $directory.FullName
        if (-not [string]::IsNullOrEmpty($relativeDir)) {
            $uninstallLines.Add(("  RMDir `"$INSTDIR\{0}`"" -f $relativeDir))
        }
    }

    Set-Content -Path $InstallIncludePath -Value $installLines -Encoding UTF8
    Set-Content -Path $UninstallIncludePath -Value $uninstallLines -Encoding UTF8
}

function Write-NsisInstaller {
    param(
        [string]$TemplatePath,
        [string]$OutputScriptPath,
        [string]$InstallerOutputPath,
        [string]$InstallIncludePath,
        [string]$UninstallIncludePath,
        [string]$VersionLabel,
        [string]$Scope
    )

    $installDir = '$LOCALAPPDATA\Programs\Animica Wallet'
    $regRoot = 'HKCU'
    $shellVarContext = 'current'
    $requestExecutionLevel = 'user'
    if ($Scope -eq "perMachine") {
        $installDir = '$PROGRAMFILES64\Animica Wallet'
        $regRoot = 'HKLM'
        $shellVarContext = 'all'
        $requestExecutionLevel = 'admin'
    }
    $uninstallRegKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\AnimicaWallet'

    $content = Get-Content -Path $TemplatePath -Raw
    $replacements = @{
        "@OUTPUT_FILE@" = $InstallerOutputPath
        "@INSTALL_DIR@" = $installDir
        "@REG_ROOT@" = $regRoot
        "@REQUEST_EXECUTION_LEVEL@" = $requestExecutionLevel
        "@SHELL_VAR_CONTEXT@" = $shellVarContext
        "@UNINSTALL_REG_KEY@" = $uninstallRegKey
        "@ICON_FILE@" = (Join-Path $WalletRoot "resources\icons\animica.ico")
        "@DISPLAY_VERSION@" = $VersionLabel
        "@VERSION_DOTTED@" = (Get-VersionDotted -VersionLabel $VersionLabel)
        "@INSTALL_FILES_INCLUDE@" = $InstallIncludePath
        "@UNINSTALL_FILES_INCLUDE@" = $UninstallIncludePath
    }

    foreach ($entry in $replacements.GetEnumerator()) {
        $content = $content.Replace($entry.Key, $entry.Value)
    }

    Set-Content -Path $OutputScriptPath -Value $content -Encoding UTF8
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
$MakeNsis = Find-MakeNsis

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

$InstallerIncludePath = Join-Path $BuildDir "installer-files.nsh"
$UninstallIncludePath = Join-Path $BuildDir "uninstaller-files.nsh"
$NsisScriptPath = Join-Path $BuildDir "installer.nsi"
$ExeInstallerPath = Join-Path $DistDir $ExeInstallerName

New-NsisFileLists -StageRoot $InstallDir -InstallIncludePath $InstallerIncludePath -UninstallIncludePath $UninstallIncludePath
Write-NsisInstaller `
    -TemplatePath (Join-Path $WalletRoot "resources\windows\installer.nsi.in") `
    -OutputScriptPath $NsisScriptPath `
    -InstallerOutputPath $ExeInstallerPath `
    -InstallIncludePath $InstallerIncludePath `
    -UninstallIncludePath $UninstallIncludePath `
    -VersionLabel $Version `
    -Scope $InstallScope

if (Test-Path $ExeInstallerPath) {
    Remove-Item -Force $ExeInstallerPath
}
& $MakeNsis $NsisScriptPath | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "NSIS installer generation failed"
}
Sign-File $ExeInstallerPath

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
Write-Log "EXE artifact:   $ExeInstallerPath"
Write-Log "ZIP artifact:   $ZipPath"
if (Test-Path (Join-Path $DistDir "AnimicaWallet-${Version}-windows-${Arch}.msi")) {
    Write-Log "MSI artifact:   $(Join-Path $DistDir "AnimicaWallet-${Version}-windows-${Arch}.msi")"
}
Write-Log "Smoke test:     .\scripts\smoke-test-windows.ps1 -WalletPath `"$InstallDir`""
Write-Log "Install scope:  $(if ($PerMachine) { "per-machine" } else { "per-user" })"
