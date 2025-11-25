<#
.SYNOPSIS
  Code-sign Animica Explorer artifacts on Windows using signtool.exe.

.DESCRIPTION
  - Signs one or more files (or all eligible files in a directory) with SHA-256.
  - Uses RFC3161 timestamping by default.
  - Verifies signatures after signing unless -SkipVerify is set.
  - Auto-discovers signtool from PATH or via vswhere (Windows SDK / Visual Studio).

.PARAMETER Path
  One or more file or directory paths. Directories will be scanned for eligible extensions.

.PARAMETER Recurse
  When Path contains directories, recurse into subdirectories to find files to sign.

.PARAMETER Extensions
  List of file extensions to sign (defaults: .exe, .msi, .msix, .appx, .dll).

.PARAMETER CertificatePath
  Path to a PFX/P12 code-signing certificate.

.PARAMETER CertPassword
  Password for the PFX/P12 certificate.

.PARAMETER CertificateThumbprint
  SHA-1 thumbprint of a certificate located in the Windows certificate store (My/CurrentUser or My/LocalMachine).

.PARAMETER StoreLocation
  Certificate store location when using -CertificateThumbprint. One of CurrentUser or LocalMachine. Default: CurrentUser.

.PARAMETER TimestampUrl
  RFC3161 timestamp server URL. Default: http://timestamp.digicert.com

.PARAMETER Description
  File description embedded in the signature. Default: "Animica Explorer"

.PARAMETER DescriptionUrl
  URL embedded in the signature. Default: https://animica.dev

.PARAMETER Append
  Append a second signature instead of replacing an existing one.

.PARAMETER SkipVerify
  Do not verify signatures after signing.

.PARAMETER EmitHash
  Write a .sha256 file next to each signed artifact.

.EXAMPLE
  # Sign all outputs in the Tauri bundle dir (non-recursive) with a PFX file
  pwsh installers/explorer-desktop/windows/codesign.ps1 `
    -Path installers/explorer-desktop/tauri/target/x86_64-pc-windows-msvc/release/bundle `
    -CertificatePath C:\secrets\codesign.pfx -CertPassword '********'

.EXAMPLE
  # Sign a single installer using a cert from the CurrentUser store (thumbprint)
  pwsh installers/explorer-desktop/windows/codesign.ps1 `
    -Path dist\Animica-Explorer-Setup.exe `
    -CertificateThumbprint '0123456789abcdef0123456789abcdef01234567'
#>

[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)]
  [string[]]$Path,

  [switch]$Recurse,

  [string[]]$Extensions = @('.exe','.msi','.msix','.appx','.dll'),

  [string]$CertificatePath = '',
  [string]$CertPassword = '',

  [string]$CertificateThumbprint = '',
  [ValidateSet('CurrentUser','LocalMachine')]
  [string]$StoreLocation = 'CurrentUser',

  [string]$TimestampUrl = 'http://timestamp.digicert.com',

  [string]$Description = 'Animica Explorer',
  [string]$DescriptionUrl = 'https://animica.dev',

  [switch]$Append,
  [switch]$SkipVerify,
  [switch]$EmitHash
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Info([string]$m){ Write-Host "[i] $m" -ForegroundColor Cyan }
function Write-Warn([string]$m){ Write-Host "[!] $m" -ForegroundColor Yellow }
function Write-Err ([string]$m){ Write-Host "[x] $m" -ForegroundColor Red }

# --- Locate signtool -----------------------------------------------------------
function Get-SignTool {
  $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Path }

  $vswhere = "${Env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
  if (Test-Path $vswhere) {
    $found = & $vswhere -latest -products * `
      -requires Microsoft.VisualStudio.Component.Windows10SDK `
      -find **\signtool.exe 2>$null | Select-Object -First 1
    if ($found) { return $found }
  }

  throw "signtool.exe not found. Install the Windows 10/11 SDK or add signtool to PATH."
}

# --- Collect files -------------------------------------------------------------
function Get-EligibleFiles([string[]]$paths,[bool]$recurse,[string[]]$exts){
  $all = @()
  foreach ($p in $paths) {
    if (Test-Path $p -PathType Leaf) {
      if ($exts -contains ([IO.Path]::GetExtension($p).ToLower())) { $all += (Get-Item $p) }
    } elseif (Test-Path $p -PathType Container) {
      $opt = @{Recurse=$false}
      if ($recurse){ $opt.Recurse = $true }
      $all += Get-ChildItem -Path $p -File @opt | Where-Object {
        $exts -contains ($_.Extension.ToLower())
      }
    } else {
      Write-Warn "Path not found: $p"
    }
  }
  $all | Sort-Object FullName -Unique
}

# --- Build signing args --------------------------------------------------------
function New-SignArgs(
  [string]$signtoolPath,
  [string]$tsUrl,
  [string]$desc,
  [string]$descUrl,
  [string]$pfxPath,
  [string]$pfxPass,
  [string]$thumb,
  [string]$storeLoc,
  [bool]$append
){
  $args = @('sign','/fd','sha256','/tr', $tsUrl, '/td','sha256','/v')
  if ($desc){     $args += @('/d',  $desc) }
  if ($descUrl){  $args += @('/du', $descUrl) }

  if ($append){ $args += '/as' }

  if ($thumb){
    # Use a cert from the specified store by thumbprint
    $args += @('/sha1', $thumb, '/s', 'My', '/sm')
    if ($storeLoc -eq 'CurrentUser') {
      # remove /sm for CurrentUser store
      $args = $args | Where-Object { $_ -ne '/sm' }
    }
  } elseif ($pfxPath) {
    $args += @('/f', $pfxPath)
    if ($pfxPass) { $args += @('/p', $pfxPass) }
  } else {
    Write-Warn "No certificate specified. Falling back to '/a' (auto-select)."
    $args += '/a'
  }

  ,$args
}

# --- Verify signature ----------------------------------------------------------
function Verify-File([string]$signtool,[string]$file){
  & $signtool verify /pa /all /v "`"$file`"" | Out-Null
}

# --- Hash helper ---------------------------------------------------------------
function Write-Hash([string]$file){
  $h = (Get-FileHash -Path $file -Algorithm SHA256).Hash.ToLower()
  $line = "$h  $(Split-Path $file -Leaf)"
  $line | Out-File -FilePath ($file + '.sha256') -Encoding ascii
  $line
}

# --- Main ----------------------------------------------------------------------
$signtool = Get-SignTool
Write-Info "Using signtool: $signtool"

$files = Get-EligibleFiles -paths $Path -recurse $Recurse -exts $Extensions
if ($files.Count -eq 0) {
  Write-Err "No eligible files found to sign."
  exit 1
}

Write-Info ("Found {0} file(s) to sign." -f $files.Count)
$signArgs = New-SignArgs -signtoolPath $signtool -tsUrl $TimestampUrl `
  -desc $Description -descUrl $DescriptionUrl `
  -pfxPath $CertificatePath -pfxPass $CertPassword `
  -thumb $CertificateThumbprint -storeLoc $StoreLocation `
  -append:$Append

$errors = 0
foreach ($f in $files) {
  try {
    Write-Info "Signing: $($f.FullName)"
    & $signtool @signArgs "`"$($f.FullName)`""

    if (-not $SkipVerify) {
      Verify-File -signtool $signtool -file $f.FullName
    }

    if ($EmitHash) {
      $line = Write-Hash -file $f.FullName
      Write-Host "  sha256: $line"
    }
  } catch {
    $errors++
    Write-Err "Failed to sign: $($f.FullName) — $($_.Exception.Message)"
  }
}

if ($errors -gt 0) {
  Write-Err "Completed with $errors error(s)."
  exit 1
}

Write-Host ""
Write-Info "✅ Signing complete."
