# package-windows-installer.ps1 - Wrap an existing staged Windows runtime into Setup.exe
#
# Usage:
#   .\scripts\package-windows-installer.ps1 `
#       [-StageDir <path>] `
#       [-OutputDir <path>] `
#       [-IsccPath <path>] `
#       [-Version <version>] `
#       [-OutputBaseName <name>]

param(
    [string]$StageDir = "",
    [string]$OutputDir = "",
    [string]$IsccPath = "",
    [string]$Version = "",
    [string]$OutputBaseName = "AnimicaWallet-Setup"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $true
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$DefaultStageDir = Join-Path $ProjectRoot "build\windows\stage"
$DefaultOutputDir = Join-Path $ProjectRoot "build\windows\installer"
$InnoScriptPath = Join-Path $ProjectRoot "packaging\windows\AnimicaWallet.iss"
$CMakeListsPath = Join-Path $ProjectRoot "CMakeLists.txt"
$InstallerIconPath = Join-Path $ProjectRoot "resources\icons\animica.ico"

function Write-Log {
    param([string]$Message)
    Write-Host "[PACKAGE] $Message" -ForegroundColor Cyan
}

function Get-FullPath {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    return [System.IO.Path]::GetFullPath($PathValue)
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

function Get-ProjectVersion {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path $Path)) {
        throw "CMakeLists.txt was not found at $Path"
    }

    $content = Get-Content -Path $Path -Raw
    $match = [regex]::Match(
        $content,
        "project\s*\(\s*[^\s\)]+\s+VERSION\s+([0-9A-Za-z\.\-_]+)",
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )

    if (-not $match.Success) {
        throw "Unable to derive the wallet version from $Path"
    }

    return $match.Groups[1].Value
}

function Get-DottedVersion {
    param([Parameter(Mandatory = $true)][string]$VersionText)

    $normalized = $VersionText.Trim()
    if ($normalized.StartsWith("v")) {
        $normalized = $normalized.Substring(1)
    }

    $normalized = $normalized.Split("-")[0]
    $parts = @($normalized.Split(".") | Where-Object { $_ -ne "" })
    if ($parts.Count -eq 0) {
        throw "Version '$VersionText' is not usable for Windows version metadata."
    }

    while ($parts.Count -lt 4) {
        $parts += "0"
    }

    if ($parts.Count -gt 4) {
        $parts = $parts[0..3]
    }

    return ($parts -join ".")
}

function Find-InnoSetupCompiler {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        $resolvedPath = Get-FullPath -PathValue $RequestedPath
        if (-not (Test-Path $resolvedPath)) {
            throw "Requested Inno Setup compiler was not found: $resolvedPath"
        }
        return $resolvedPath
    }

    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = New-Object System.Collections.Generic.List[string]
    if (${env:ProgramFiles(x86)}) {
        $candidates.Add((Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"))
        $candidates.Add((Join-Path ${env:ProgramFiles(x86)} "Inno Setup 5\ISCC.exe"))
    }
    if ($env:ProgramFiles) {
        $candidates.Add((Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"))
        $candidates.Add((Join-Path $env:ProgramFiles "Inno Setup 5\ISCC.exe"))
    }

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    throw "Inno Setup 6 was not found. Install Inno Setup and ensure ISCC.exe is on PATH or pass -IsccPath."
}

function Assert-StageFile {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )

    $candidate = Join-Path $Root $RelativePath
    if (-not (Test-Path $candidate)) {
        throw "Staged runtime is incomplete. Missing required path: $candidate"
    }
}

if (-not $StageDir) {
    $StageDir = $DefaultStageDir
}

if (-not $OutputDir) {
    $OutputDir = $DefaultOutputDir
}

$StageDir = Get-FullPath -PathValue $StageDir
$OutputDir = Get-FullPath -PathValue $OutputDir
$InnoScriptPath = Get-FullPath -PathValue $InnoScriptPath
$InstallerPath = Join-Path $OutputDir ($OutputBaseName + ".exe")

if (-not $Version) {
    $Version = Get-ProjectVersion -Path $CMakeListsPath
}

$VersionInfoVersion = Get-DottedVersion -VersionText $Version
$Iscc = Find-InnoSetupCompiler -RequestedPath $IsccPath

Write-Log "Validating staged runtime at $StageDir"
if (-not (Test-Path $StageDir -PathType Container)) {
    throw "Stage directory not found: $StageDir. Run .\scripts\build-windows.ps1 first."
}

Assert-StageFile -Root $StageDir -RelativePath "animica-wallet.exe"
Assert-StageFile -Root $StageDir -RelativePath "Qt6Core.dll"
Assert-StageFile -Root $StageDir -RelativePath "platforms\qwindows.dll"

if (-not (Test-Path $InnoScriptPath -PathType Leaf)) {
    throw "Inno Setup script not found: $InnoScriptPath"
}

if (Test-Path (Join-Path $StageDir "vc_redist.x64.exe")) {
    Write-Log "Detected vc_redist.x64.exe in stage. It will be packaged with the staged tree but not executed automatically."
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
if (Test-Path $InstallerPath) {
    Remove-Item -Force $InstallerPath
}

$CompilerArgs = @(
    "/DStageDir=$StageDir",
    "/DOutputDir=$OutputDir",
    "/DOutputBaseFilename=$OutputBaseName",
    "/DAppVersion=$Version",
    "/DVersionInfoVersion=$VersionInfoVersion"
)

if (Test-Path $InstallerIconPath -PathType Leaf) {
    $CompilerArgs += "/DInstallerIconFile=$InstallerIconPath"
}

$CompilerArgs += $InnoScriptPath

Write-Log "Compiling Inno Setup installer with $Iscc"
Invoke-ExternalCommand `
    -FilePath $Iscc `
    -ArgumentList $CompilerArgs `
    -FailureMessage "Inno Setup compilation failed"

if (-not (Test-Path $InstallerPath -PathType Leaf)) {
    throw "ISCC completed but the expected installer was not found: $InstallerPath"
}

Write-Log ""
Write-Log "Installer packaging completed successfully"
Write-Log "  Version:   $Version"
Write-Log "  Stage dir: $StageDir"
Write-Log "  Installer: $InstallerPath"
