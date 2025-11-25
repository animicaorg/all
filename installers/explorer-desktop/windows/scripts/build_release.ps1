<#
.SYNOPSIS
  Build the Animica Explorer (Tauri) Windows release and optionally code-sign artifacts.

.DESCRIPTION
  - Invokes `cargo tauri build` for the selected target (x64 / arm64).
  - Supports Offline or URL mode via environment variables.
  - Locates produced installers (NSIS .exe and/or .msi) and code-signs them with signtool.
  - Emits SHA256 checksums and verifies signatures.

.PARAMETER Mode
  'offline' (default) to bundle the UI, or 'url' to point to a remote site.

.PARAMETER Url
  Remote URL to load in URL mode (ignored when Mode=offline). Example: https://explorer.animica.dev

.PARAMETER Target
  'x64' (default) or 'arm64' — maps to x86_64-pc-windows-msvc / aarch64-pc-windows-msvc.

.PARAMETER Profile
  'release' (default) or 'debug' — passed through to cargo.

.PARAMETER Sign
  Switch to enable code signing using signtool.exe.

.PARAMETER CertificatePath
  Path to a .pfx (or .p12) file for signing. Requires -CertPassword if protected.

.PARAMETER CertPassword
  Password for the provided PFX file.

.PARAMETER CertificateThumbprint
  SHA-1 thumbprint of a certificate in the user/machine 'My' store. Alternative to -CertificatePath.

.PARAMETER TimestampUrl
  RFC3161 timestamp server (default: http://timestamp.digicert.com).

.PARAMETER OutDir
  Optional path to copy signed artifacts and checksums.

.EXAMPLE
  # Build offline x64 and sign with a PFX cert
  pwsh installers/explorer-desktop/windows/scripts/build_release.ps1 `
    -Mode offline -Target x64 -Sign `
    -CertificatePath "C:\secrets\code_signing.pfx" -CertPassword "********"

.EXAMPLE
  # Build URL mode (dev/beta) without signing
  pwsh installers/explorer-desktop/windows/scripts/build_release.ps1 -Mode url -Url "https://staging.explorer.animica.dev"
#>

[CmdletBinding()]
param(
  [ValidateSet('offline','url')]
  [string]$Mode = 'offline',

  [string]$Url = '',

  [ValidateSet('x64','arm64')]
  [string]$Target = 'x64',

  [ValidateSet('release','debug')]
  [string]$Profile = 'release',

  [switch]$Sign,

  [string]$CertificatePath = '',
  [string]$CertPassword = '',
  [string]$CertificateThumbprint = '',
  [string]$TimestampUrl = 'http://timestamp.digicert.com',

  [string]$OutDir = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Info([string]$msg) { Write-Host "[i] $msg" -ForegroundColor Cyan }
function Write-Warn([string]$msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Err ([string]$msg) { Write-Host "[x] $msg" -ForegroundColor Red }

# Resolve directories
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir '..\..\..') | ForEach-Object { $_.Path }
$TauriDir  = Join-Path $RepoRoot 'installers\explorer-desktop\tauri'

if (-not (Test-Path $TauriDir)) {
  Write-Err "Tauri project not found: $TauriDir"
  exit 1
}

# Tool checks ---------------------------------------------------------------
function Ensure-Tool($name) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
    Write-Err "Missing tool: $name"
    exit 1
  }
}

Ensure-Tool cargo

if ($Sign) {
  # signtool is part of Windows SDK; try both native and vswhere lookup
  $signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if (-not $signtool) {
    $vswhere = "${Env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
      $sdk = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.Windows10SDK -find **\signtool.exe 2>$null | Select-Object -First 1
      if ($sdk) { $signtool = Get-Item $sdk }
    }
  }
  if (-not $signtool) {
    Write-Err "signtool.exe not found. Install Windows 10/11 SDK."
    exit 1
  }
}

# Build args ----------------------------------------------------------------
$Triple = if ($Target -eq 'arm64') { 'aarch64-pc-windows-msvc' } else { 'x86_64-pc-windows-msvc' }
$IsRelease = ($Profile -eq 'release')

# Env for app mode
$env:EXPLORER_MODE = $Mode
if ($Mode -eq 'url') {
  if ([string]::IsNullOrWhiteSpace($Url)) {
    Write-Err "-Url is required when -Mode url"
    exit 1
  }
  $env:EXPLORER_URL = $Url
}

# Build ---------------------------------------------------------------------
Push-Location $TauriDir
try {
  Write-Info "Building Tauri app ($Profile, $Target → $Triple)…"
  $buildArgs = @('tauri','build','--target', $Triple)
  if (-not $IsRelease) { $buildArgs += '--debug' }

  # Prefer cargo-tauri subcommand; fallback to 'npx tauri' is not ideal in CI
  cargo @buildArgs
}
finally {
  Pop-Location
}

# Locate artifacts ----------------------------------------------------------
$BundleRoot = Join-Path $TauriDir "target\$Triple\release\bundle"
if (-not (Test-Path $BundleRoot)) {
  Write-Err "Bundle output not found: $BundleRoot"
  exit 1
}

$Candidates = @()
# NSIS installer (default for Tauri on Windows)
$nsisDir = Join-Path $BundleRoot 'nsis'
if (Test-Path $nsisDir) {
  $Candidates += Get-ChildItem $nsisDir -Filter '*.exe' | Sort-Object LastWriteTime -Descending
}
# MSI (if WiX/other bundler configured)
$msiDir = Join-Path $BundleRoot 'msi'
if (Test-Path $msiDir) {
  $Candidates += Get-ChildItem $msiDir -Filter '*.msi' | Sort-Object LastWriteTime -Descending
}
# Raw app exe (portable)
$winDir = Join-Path $BundleRoot 'windows'
if (Test-Path $winDir) {
  $Candidates += Get-ChildItem $winDir -Filter '*.exe' | Sort-Object LastWriteTime -Descending
}

if ($Candidates.Count -eq 0) {
  Write-Err "No artifacts found in $BundleRoot"
  exit 1
}

Write-Info "Artifacts:"
$Candidates | ForEach-Object { Write-Host "  - $($_.FullName)" }

# Signing -------------------------------------------------------------------
function Sign-Artifact {
  param(
    [Parameter(Mandatory=$true)][string]$Path
  )
  $global:signtool = Get-Command signtool.exe -ErrorAction Stop
  $common = @('sign','/fd','sha256','/tr', $TimestampUrl, '/td','sha256','/v')

  if ($CertificateThumbprint) {
    & $signtool $common '/sha1' $CertificateThumbprint $Path
  }
  elseif ($CertificatePath) {
    $args = @($common + @('/f', $CertificatePath))
    if ($CertPassword) { $args += @('/p', $CertPassword) }
    & $signtool @args $Path
  }
  else {
    Write-Err "Signing requested but no -CertificateThumbprint or -CertificatePath provided."
    exit 1
  }

  # Verify signature
  & $signtool verify /pa /all /v $Path | Out-Null
}

if ($Sign) {
  Write-Info "Code signing artifacts…"
  foreach ($f in $Candidates) {
    Sign-Artifact -Path $f.FullName
  }
}

# Hashes & outputs ----------------------------------------------------------
function Write-Hash {
  param([string]$Path)
  $h = (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLower()
  $line = "$h  $(Split-Path $Path -Leaf)"
  $line | Out-File -FilePath ($Path + '.sha256') -Encoding ascii
  return $line
}

$Hashes = @()
foreach ($f in $Candidates) {
  $hashLine = Write-Hash -Path $f.FullName
  $Hashes += $hashLine
}

Write-Host ""
Write-Info "SHA256 checksums:"
$Hashes | ForEach-Object { Write-Host "  $_" }

if ($OutDir) {
  New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
  foreach ($f in $Candidates) {
    Copy-Item $f.FullName -Destination (Join-Path $OutDir (Split-Path $f.FullName -Leaf)) -Force
    Copy-Item ($f.FullName + '.sha256') -Destination (Join-Path $OutDir ((Split-Path $f.FullName -Leaf) + '.sha256')) -Force
  }
  Write-Info "Copied artifacts to: $OutDir"
}

Write-Host ""
Write-Info "✅ Build complete."
