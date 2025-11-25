<#
  Animica Wallet — MakeMSIX.ps1
  Packs a Windows MSIX using MakeAppx and signs it with signtool.

  Typical usage:
    pwsh installers/wallet/windows/msix/MakeMSIX.ps1 `
      -LayoutDir dist/windows/stable/msix-layout `
      -OutputMsix dist/windows/stable/Animica-Wallet_1.2.3.0_x64.msix `
      -PfxPath $env:CSC_PFX_PATH -PfxPassword $env:CSC_PFX_PASSWORD

  Or with a cert in the Windows cert store:
    pwsh installers/wallet/windows/msix/MakeMSIX.ps1 `
      -LayoutDir dist/windows/stable/msix-layout `
      -OutputMsix dist/windows/stable/Animica-Wallet_1.2.3.0_x64.msix `
      -SubjectName "Animica Labs, Inc."

  Notes:
    • The layout directory MUST contain AppxManifest.xml at its root.
      If you only have a template (e.g., Package.appxmanifest), render/copy it first.
    • Set -NoSign to skip signing (not recommended).
    • Environment fallbacks for signing are supported (CSC_PFX_PATH, CSC_PFX_PASSWORD,
      CSC_SUBJECT_NAME, CSC_THUMBPRINT, CSC_TSA_URL).
#>

[CmdletBinding(PositionalBinding = $false)]
param(
  # Required input/output
  [Parameter(Mandatory = $true)]
  [ValidateScript({ Test-Path $_ -PathType Container })]
  [string]$LayoutDir,

  [Parameter(Mandatory = $true)]
  [string]$OutputMsix,

  # Optional: if AppxManifest.xml is missing in LayoutDir, copy this file into place.
  [ValidateScript({ Test-Path $_ -PathType Leaf })]
  [string]$ManifestPath,

  # Overwrite existing output
  [switch]$Overwrite,

  # Signing controls
  [switch]$NoSign,
  [switch]$VerifyAfter,

  [string]$PfxPath      = $env:CSC_PFX_PATH,
  [string]$PfxPassword  = $env:CSC_PFX_PASSWORD,
  [string]$SubjectName  = $env:CSC_SUBJECT_NAME,
  [string]$Thumbprint   = $env:CSC_THUMBPRINT,
  [string]$TimestampUrl = $(if ($env:CSC_TSA_URL) { $env:CSC_TSA_URL } else { "https://timestamp.digicert.com" }),

  [ValidateSet("sha256","sha1")]
  [string]$FileDigest = "sha256",
  [ValidateSet("sha256","sha1")]
  [string]$TimestampDigest = "sha256"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Log([string]$m){ Write-Host "[MakeMSIX] $m" -ForegroundColor Cyan }
function Warn([string]$m){ Write-Host "[warn    ] $m" -ForegroundColor Yellow }
function Fail([string]$m){ Write-Host "[fail    ] $m" -ForegroundColor Red; throw $m }

function Need-Tool([string]$exe, [string]$hint){
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
    Fail "Missing required tool '$exe'. $hint"
  }
}

function Ensure-Manifest([string]$dir, [string]$fallback){
  $manifest = Join-Path $dir 'AppxManifest.xml'
  if (Test-Path -LiteralPath $manifest) { return $manifest }
  if ($fallback) {
    Log "AppxManifest.xml not found. Copying from: $fallback"
    Copy-Item -LiteralPath $fallback -Destination $manifest -Force
    return $manifest
  }
  Fail "AppxManifest.xml not found in layout and no -ManifestPath provided."
}

function Pack-MSIX([string]$dir, [string]$out, [switch]$overwrite){
  if ((Test-Path -LiteralPath $out) -and -not $overwrite) {
    Fail "Output already exists: $out (use -Overwrite to replace)"
  }
  if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
  $args = @('pack', '/d', $dir, '/p', $out, '/o')
  $p = Start-Process -FilePath 'MakeAppx' -ArgumentList $args -Wait -PassThru -NoNewWindow
  if ($p.ExitCode -ne 0) { Fail "MakeAppx failed (exit $($p.ExitCode))" }
}

function Sign-File([string]$path){
  $args = @('sign','/fd', $FileDigest, '/tr', $TimestampUrl, '/td', $TimestampDigest)

  if ($PfxPath) {
    if (-not (Test-Path -LiteralPath $PfxPath)) { Fail "PFX not found: $PfxPath" }
    $args += @('/f', $PfxPath)
    if ($PfxPassword) { $args += @('/p', $PfxPassword) }
  } elseif ($Thumbprint) {
    $args += @('/sha1', $Thumbprint)
  } elseif ($SubjectName) {
    $args += @('/n', $SubjectName)
  } else {
    Fail "No signing credential provided. Use -PfxPath, -SubjectName, or -Thumbprint, or set -NoSign."
  }

  $args += @($path)
  $p = Start-Process -FilePath 'signtool.exe' -ArgumentList $args -Wait -PassThru -NoNewWindow
  if ($p.ExitCode -ne 0) { Fail "signtool sign failed (exit $($p.ExitCode))" }
}

function Verify-File([string]$path){
  $args = @('verify','/pa','/all','/v', $path)
  $p = Start-Process -FilePath 'signtool.exe' -ArgumentList $args -Wait -PassThru -NoNewWindow
  if ($p.ExitCode -ne 0) { Fail "signtool verify failed (exit $($p.ExitCode))" }
}

function Print-Hash([string]$file){
  $h = Get-FileHash -Algorithm SHA256 -LiteralPath $file
  Write-Host ("SHA256  {0}  {1}" -f $h.Hash.ToLower(), $file) -ForegroundColor DarkGray
}

# --- Tooling checks
Need-Tool 'MakeAppx' 'Install the Windows 10/11 SDK.'
if (-not $NoSign) { Need-Tool 'signtool.exe' 'Install the Windows 10/11 SDK.' }

# --- Ensure manifest present
$manifest = Ensure-Manifest -dir $LayoutDir -fallback $ManifestPath
Log "Using manifest: $manifest"

# --- Pack
$newDir = Split-Path -Parent $OutputMsix
if ($newDir) { New-Item -ItemType Directory -Force -Path $newDir | Out-Null }
Log "Packing MSIX → $OutputMsix"
Pack-MSIX -dir $LayoutDir -out $OutputMsix -overwrite:$Overwrite

# --- Sign (unless disabled)
if (-not $NoSign) {
  Log "Signing MSIX…"
  Sign-File -path $OutputMsix
  if ($VerifyAfter) {
    Log "Verifying signature…"
    Verify-File -path $OutputMsix
  }
} else {
  Warn "Signing disabled (-NoSign). This package will not pass SmartScreen or enterprise policy."
}

# --- Hash summary
Print-Hash -file $OutputMsix
Log "Done ✔"
