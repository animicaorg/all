# release-windows.ps1 - Build, stage, and package a native Windows installer for Animica Wallet
#
# Usage:
#   .\scripts\release-windows.ps1 [-Debug] [-Clean] [-QtPath <path>] [-Jobs <n>] [-IsccPath <path>] [-Version <version>]

param(
    [switch]$Debug = $false,
    [switch]$Clean = $false,
    [string]$QtPath = "",
    [int]$Jobs = 0,
    [string]$IsccPath = "",
    [string]$Version = "",
    [switch]$PerMachine = $false,
    [switch]$Sign = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$BuildScript = Join-Path $ScriptDir "build-windows.ps1"
$PackageScript = Join-Path $ScriptDir "package-windows-installer.ps1"
$StageDir = Join-Path $ProjectRoot "build\windows\stage"
$InstallerPath = Join-Path $ProjectRoot "build\windows\installer\AnimicaWallet-Setup.exe"

function Write-Log {
    param([string]$Message)
    Write-Host "[RELEASE] $Message" -ForegroundColor Cyan
}

if ($Sign) {
    throw "The native Windows release flow does not implement code signing in this script. Omit -Sign or add signing as a separate step."
}

if ($PerMachine) {
    Write-Log "-PerMachine was specified. The Inno Setup installer already defaults to Program Files and requires elevation."
}

$BuildArgs = @()
if ($Debug) {
    $BuildArgs += "-Debug"
}
if ($Clean) {
    $BuildArgs += "-Clean"
}
if ($QtPath) {
    $BuildArgs += @("-QtPath", $QtPath)
}
if ($Jobs -gt 0) {
    $BuildArgs += @("-Jobs", $Jobs)
}

$PackageArgs = @()
if ($IsccPath) {
    $PackageArgs += @("-IsccPath", $IsccPath)
}
if ($Version) {
    $PackageArgs += @("-Version", $Version)
}

Write-Log "Starting native Windows release flow"
Write-Log "Step 1/2: build and stage runtime"
& $BuildScript @BuildArgs

Write-Log "Step 2/2: compile Setup.exe with Inno Setup"
& $PackageScript @PackageArgs

if (-not (Test-Path $InstallerPath -PathType Leaf)) {
    throw "Release flow completed without producing the expected installer: $InstallerPath"
}

Write-Log ""
Write-Log "Release completed successfully"
Write-Log "  Staged runtime: $StageDir"
Write-Log "  Installer:      $InstallerPath"
