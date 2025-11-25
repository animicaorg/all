<#
.SYNOPSIS
  Package Animica Explorer as an MSIX and (optionally) code-sign it.

.DESCRIPTION
  - Stages a proper MSIX directory layout:
        StagingRoot\
          AppxManifest.xml
          App\animica-explorer.exe (and DLLs)
          Assets\*.png
  - Packs it using Windows SDK `makeappx.exe`.
  - Optionally signs with `signtool.exe` (PFX or cert store thumbprint).
  - Emits SHA256 checksum and verifies signature.

.PARAMETER AppBinary
  Path to the built Explorer executable (animica-explorer.exe). If omitted, the script
  will try to auto-discover under the Tauri bundle output.

.PARAMETER Manifest
  Path to Package.appxmanifest (template filled with your Publisher DN & Identity).

.PARAMETER AssetsDir
  Directory containing MSIX logo assets (Square150x150Logo.png, Square44x44Logo.png, etc.).

.PARAMETER Out
  Output .msix path. Defaults to ./Animica-Explorer.msix in the repo root.

.PARAMETER Version
  Optional override for the Identity@Version in the manifest (e.g., 0.1.4.0).

.PARAMETER Publisher
  Optional override for Identity@Publisher in the manifest (must EXACTLY match your cert subject DN).

.PARAMETER StagingDir
  Optional custom staging directory. Defaults to a temporary directory.

# Signing options
.PARAMETER Sign
  Switch to enable signing.

.PARAMETER CertificatePath
  Path to .pfx/.p12 for signing.

.PARAMETER CertPassword
  Password for the PFX.

.PARAMETER CertificateThumbprint
  SHA-1 thumbprint of a cert in CurrentUser/LocalMachine\My store.

.PARAMETER StoreLocation
  CurrentUser (default) or LocalMachine for -CertificateThumbprint.

.PARAMETER TimestampUrl
  RFC3161 timestamp server (default http://timestamp.digicert.com).

.EXAMPLE
  # Pack and sign with PFX
  pwsh installers/explorer-desktop/windows/msix/MakeMSIX.ps1 `
    -Manifest installers/explorer-desktop/windows/msix/Package.appxmanifest `
    -AssetsDir installers/explorer-desktop/windows/msix/Assets `
    -Sign -CertificatePath C:\secrets\codesign.pfx -CertPassword '********'

.EXAMPLE
  # Pack only (no signing)
  pwsh installers/explorer-desktop/windows/msix/MakeMSIX.ps1 -Out dist\Animica-Explorer.msix
#>

[CmdletBinding()]
param(
  [string]$AppBinary = '',
  [Parameter(Mandatory=$true)][string]$Manifest,
  [Parameter(Mandatory=$true)][string]$AssetsDir,
  [string]$Out = '',
  [string]$Version = '',
  [string]$Publisher = '',
  [string]$StagingDir = '',

  [switch]$Sign,
  [string]$CertificatePath = '',
  [string]$CertPassword = '',
  [string]$CertificateThumbprint = '',
  [ValidateSet('CurrentUser','LocalMachine')][string]$StoreLocation = 'CurrentUser',
  [string]$TimestampUrl = 'http://timestamp.digicert.com'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Info([string]$m){ Write-Host "[i] $m" -ForegroundColor Cyan }
function Write-Warn([string]$m){ Write-Host "[!] $m" -ForegroundColor Yellow }
function Write-Err ([string]$m){ Write-Host "[x] $m" -ForegroundColor Red }

# --- Tool discovery ------------------------------------------------------------
function Find-MakeAppx {
  $cmd = Get-Command makeappx.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Path }

  $kits = Join-Path "${Env:ProgramFiles(x86)}" 'Windows Kits\10\bin'
  if (Test-Path $kits) {
    $candidates = Get-ChildItem $kits -Directory -ErrorAction SilentlyContinue |
      ForEach-Object { Join-Path $_.FullName 'x64\makeappx.exe' } |
      Where-Object { Test-Path $_ } |
      Sort-Object { Split-Path (Split-Path $_ -Parent) -Leaf } -Descending
    if ($candidates.Count -gt 0) { return $candidates[0] }
  }
  throw "makeappx.exe not found. Install Windows 10/11 SDK or add to PATH."
}

function Find-SignTool {
  $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Path }

  $vswhere = "${Env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
  if (Test-Path $vswhere) {
    $found = & $vswhere -latest -products * `
      -requires Microsoft.VisualStudio.Component.Windows10SDK `
      -find **\signtool.exe 2>$null | Select-Object -First 1
    if ($found) { return $found }
  }

  $kits = Join-Path "${Env:ProgramFiles(x86)}" 'Windows Kits\10\bin'
  if (Test-Path $kits) {
    $candidates = Get-ChildItem $kits -Directory -ErrorAction SilentlyContinue |
      ForEach-Object { Join-Path $_.FullName 'x64\signtool.exe' } |
      Where-Object { Test-Path $_ } |
      Sort-Object { Split-Path (Split-Path $_ -Parent) -Leaf } -Descending
    if ($candidates.Count -gt 0) { return $candidates[0] }
  }

  throw "signtool.exe not found. Install Windows 10/11 SDK or add to PATH."
}

$MakeAppx = Find-MakeAppx
Write-Info "Using makeappx: $MakeAppx"

if ($Sign) { $SignTool = Find-SignTool; Write-Info "Using signtool: $SignTool" }

# --- Resolve repo paths & autodetect binary -----------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir '..\..\..') | % { $_.Path }
$TauriOut  = Join-Path $RepoRoot 'installers\explorer-desktop\tauri\target'

function AutoDetect-AppBinary {
  $patterns = @(
    'x86_64-pc-windows-msvc\release\bundle\windows\*.exe',
    'aarch64-pc-windows-msvc\release\bundle\windows\*.exe'
  )
  foreach ($p in $patterns) {
    $glob = Join-Path $TauriOut $p
    $cand = Get-ChildItem $glob -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($cand) { return $cand.FullName }
  }
  return $null
}

if (-not $AppBinary) {
  $AppBinary = AutoDetect-AppBinary
  if (-not $AppBinary) { Write-Err "AppBinary not provided and auto-detect failed."; exit 1 }
  Write-Info "Auto-detected AppBinary: $AppBinary"
}

if (-not (Test-Path $AppBinary)) { Write-Err "AppBinary not found: $AppBinary"; exit 1 }
if (-not (Test-Path $Manifest))  { Write-Err "Manifest not found: $Manifest";   exit 1 }
if (-not (Test-Path $AssetsDir)) { Write-Err "AssetsDir not found: $AssetsDir"; exit 1 }

# --- Prepare staging -----------------------------------------------------------
$TempDir = if ($StagingDir) { $StagingDir } else { New-Item -ItemType Directory -Path ([IO.Path]::GetTempPath()) -Name ("animica-msix-" + [guid]::NewGuid()) | % { $_.FullName } }
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

$StageRoot   = $TempDir
$StageApp    = Join-Path $StageRoot 'App'
$StageAssets = Join-Path $StageRoot 'Assets'
New-Item -ItemType Directory -Force -Path $StageApp,$StageAssets | Out-Null

# Copy executable & DLLs from its folder
$AppDir = Split-Path $AppBinary -Parent
Copy-Item $AppBinary $StageApp -Force
Get-ChildItem $AppDir -Filter '*.dll' -ErrorAction SilentlyContinue | Copy-Item -Destination $StageApp -Force

# Copy Assets
Get-ChildItem $AssetsDir -File | Copy-Item -Destination $StageAssets -Force

# Prepare manifest (apply overrides if requested)
$StageManifest = Join-Path $StageRoot 'AppxManifest.xml'
[xml]$xml = Get-Content -LiteralPath $Manifest
$ns = New-Object System.Xml.XmlNamespaceManager($xml.NameTable)
$ns.AddNamespace('m', 'http://schemas.microsoft.com/appx/manifest/foundation/windows10')

$identity = $xml.Package.Identity
if ($Version)   { $identity.Version   = $Version }
if ($Publisher) { $identity.Publisher = $Publisher }
$xml.Save($StageManifest)

Write-Info "Staged MSIX layout at: $StageRoot"
Write-Info "  - App\\$(Split-Path $AppBinary -Leaf)"
Write-Info "  - Assets\\*"
Write-Info "  - AppxManifest.xml"

# --- Pack MSIX -----------------------------------------------------------------
if (-not $Out) {
  $Out = Join-Path $RepoRoot 'Animica-Explorer.msix'
}
$OutDir = Split-Path $Out -Parent
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Info "Packing MSIX → $Out"
/& $MakeAppx pack /d "$StageRoot" /p "$Out" /o | Write-Verbose

if (-not (Test-Path $Out)) { Write-Err "makeappx did not produce $Out"; exit 1 }

# --- Sign (optional) -----------------------------------------------------------
function Sign-File([string]$file) {
  $args = @('sign','/fd','sha256','/tr', $TimestampUrl, '/td','sha256','/v')
  if ($CertificateThumbprint) {
    $args += @('/sha1', $CertificateThumbprint, '/s', 'My', '/sm')
    if ($StoreLocation -eq 'CurrentUser') {
      $args = $args | Where-Object { $_ -ne '/sm' }
    }
  } elseif ($CertificatePath) {
    $args += @('/f', $CertificatePath)
    if ($CertPassword) { $args += @('/p', $CertPassword) }
  } else {
    Write-Err "Signing requested but no -CertificateThumbprint or -CertificatePath provided."
    exit 1
  }
  & $SignTool @args "`"$file`""
  & $SignTool verify /pa /all /v "`"$file`"" | Out-Null
}

if ($Sign) {
  Write-Info "Signing MSIX…"
  Sign-File -file $Out
}

# --- Emit SHA256 ---------------------------------------------------------------
$hash = (Get-FileHash -Path $Out -Algorithm SHA256).Hash.ToLower()
$shaLine = "$hash  $(Split-Path $Out -Leaf)"
$shaPath = "$Out.sha256"
$shaLine | Out-File -FilePath $shaPath -Encoding ascii
Write-Info "SHA256: $shaLine"

Write-Host ""
Write-Info "✅ MSIX ready: $Out"
Write-Host "Checksum file: $shaPath"
Write-Host "Staging dir:   $StageRoot"
Write-Host ""
Write-Warn "Remember: The Identity/Publisher in the manifest must match your signing cert DN exactly."
