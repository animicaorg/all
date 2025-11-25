<# 
  Animica — codesign.ps1
  Thin, reliable wrappers around signtool.exe for signing and verifying Windows artifacts.

  Features
  - Signs one or many files (*.exe, *.dll, *.msi, *.msix, *.appx, *.msixbundle, *.appxbundle)
  - Uses PFX (path + password), or a cert from the Windows cert store (by subject name or thumbprint)
  - RFC3161 timestamping (default DigiCert TSA) with SHA-256
  - Batch signing with recurse & glob filters
  - Post-sign verification and SHA-256 hashing summary
  - Designed for CI: picks defaults from environment variables if not provided

  Environment variables (optional)
    CSC_PFX_PATH       -> path to .pfx
    CSC_PFX_PASSWORD   -> password for .pfx
    CSC_SUBJECT_NAME   -> cert subject common name (fallback if no PFX)
    CSC_THUMBPRINT     -> cert thumbprint (fallback if no PFX/subject)
    CSC_TSA_URL        -> timestamp server (default: https://timestamp.digicert.com)

  Examples
    # Sign a single MSIX using a PFX
    pwsh installers/wallet/windows/codesign.ps1 -Path dist\Animica-Wallet_1.2.3.0_x64.msix -PfxPath $env:CSC_PFX_PATH -PfxPassword $env:CSC_PFX_PASSWORD

    # Sign all EXE/DLL under build folder using a store cert subject; verify after
    pwsh installers/wallet/windows/codesign.ps1 -Path build\windows\x64\runner\Release -Recurse -Filter "*.exe,*.dll" -SubjectName "Animica Labs, Inc." -VerifyAfter

    # Sign with a store cert thumbprint and a custom timestamp authority
    pwsh installers/wallet/windows/codesign.ps1 -Path dist\setup.exe -Thumbprint "ABCD1234..." -TimestampUrl "https://timestamp.globalsign.com/?signature=sha2"

  Notes
    - For MSIX/Appx, sign the package (.msix / .appx) — not the individual inner files.
    - Always timestamp; unsigned or untimestamped binaries may fail SmartScreen and compliance gates.
#>

[CmdletBinding(PositionalBinding = $false)]
param(
  # One or more files or directories. Directories are expanded per -Recurse and -Filter.
  [Parameter(Mandatory = $true)]
  [string[]]$Path,

  # Recurse when a directory path is provided.
  [switch]$Recurse,

  # Comma-separated glob filters (applied to directories). Default covers common artifacts.
  [string]$Filter = "*.exe,*.dll,*.msi,*.msix,*.msixbundle,*.appx,*.appxbundle",

  # Signing credentials (one of PfxPath|SubjectName|Thumbprint must be available via args or env).
  [string]$PfxPath      = $env:CSC_PFX_PATH,
  [string]$PfxPassword  = $env:CSC_PFX_PASSWORD,
  [string]$SubjectName  = $env:CSC_SUBJECT_NAME,
  [string]$Thumbprint   = $env:CSC_THUMBPRINT,

  # Timestamp service (RFC3161)
  [string]$TimestampUrl = $(if ($env:CSC_TSA_URL) { $env:CSC_TSA_URL } else { "https://timestamp.digicert.com" }),

  # Digests
  [ValidateSet("sha256","sha1")]
  [string]$FileDigest = "sha256",
  [ValidateSet("sha256","sha1")]
  [string]$TimestampDigest = "sha256",

  # Append signature instead of replacing (rare; used for dual-sign scenarios)
  [switch]$Append,

  # Verify signature after signing
  [switch]$VerifyAfter,

  # Continue on error (best-effort batch)
  [switch]$ContinueOnError,

  # Dry-run: list targets and planned command but do not execute signtool
  [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Log    ([string]$m){ Write-Host "[codesign] $m" -ForegroundColor Cyan }
function Warn   ([string]$m){ Write-Host "[warn    ] $m" -ForegroundColor Yellow }
function ErrorX ([string]$m){ Write-Host "[error   ] $m" -ForegroundColor Red; throw $m }

function Need-Tool([string]$exe, [string]$hint){
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
    ErrorX "Missing required tool '$exe'. $hint"
  }
}

function Resolve-Targets([string[]]$roots, [switch]$recurse, [string]$filterCsv){
  $filters = $filterCsv.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" }
  $targets = New-Object System.Collections.Generic.List[string]
  foreach ($r in $roots) {
    if (Test-Path -LiteralPath $r -PathType Leaf) {
      $targets.Add((Resolve-Path -LiteralPath $r).Path) | Out-Null
      continue
    }
    if (Test-Path -LiteralPath $r -PathType Container) {
      foreach ($f in $filters) {
        $opt = @{}
        if ($recurse) { $opt.Recurse = $true }
        Get-ChildItem -LiteralPath $r -Filter $f -File @opt | ForEach-Object {
          $targets.Add($_.FullName) | Out-Null
        }
      }
      continue
    }
    Warn "Path not found: $r"
  }
  # Dedup + natural sort
  $targets = $targets.ToArray() | Sort-Object -Unique
  return ,$targets
}

function Build-SignArgs([hashtable]$cfg, [string]$file){
  $args = @('sign','/fd', $cfg.FileDigest, '/tr', $cfg.TimestampUrl, '/td', $cfg.TimestampDigest)
  if ($cfg.Append) { $args += '/as' }

  if ($cfg.PfxPath) {
    $args += @('/f', $cfg.PfxPath)
    if ($cfg.PfxPassword) { $args += @('/p', $cfg.PfxPassword) }
  } elseif ($cfg.Thumbprint) {
    $args += @('/sha1', $cfg.Thumbprint)
  } elseif ($cfg.SubjectName) {
    $args += @('/n', $cfg.SubjectName)
  } else {
    ErrorX "No signing credential provided. Supply -PfxPath, -SubjectName, or -Thumbprint (or via env)."
  }

  $args += @($file)
  return ,$args
}

function Invoke-Sign([hashtable]$cfg, [string]$file){
  $args = Build-SignArgs -cfg $cfg -file $file
  if ($cfg.DryRun) {
    Log "DRY-RUN sign: signtool $($args -join ' ')"
    return $true
  }
  $p = Start-Process -FilePath 'signtool.exe' -ArgumentList $args -Wait -PassThru -NoNewWindow
  if ($p.ExitCode -ne 0) {
    if ($cfg.ContinueOnError) {
      Warn "signtool failed for: $file (exit $($p.ExitCode)); continuing due to -ContinueOnError"
      return $false
    }
    ErrorX "signtool failed for: $file (exit $($p.ExitCode))"
  }
  return $true
}

function Invoke-Verify([string]$file){
  $args = @('verify','/pa','/all','/v', $file)
  $p = Start-Process -FilePath 'signtool.exe' -ArgumentList $args -Wait -PassThru -NoNewWindow
  if ($p.ExitCode -ne 0) {
    ErrorX "Signature verification failed: $file (exit $($p.ExitCode))"
  }
}

function Print-Hash([string]$file){
  try {
    $h = Get-FileHash -Algorithm SHA256 -LiteralPath $file
    "{0}  {1}" -f $h.Hash.ToLower(), $file
  } catch { "????????  $file" }
}

# --- Main ---
Need-Tool 'signtool.exe' 'Install Windows 10/11 SDK (signtool) and ensure it is on PATH.'

$cfg = @{
  PfxPath         = $PfxPath
  PfxPassword     = $PfxPassword
  SubjectName     = $SubjectName
  Thumbprint      = $Thumbprint
  TimestampUrl    = $TimestampUrl
  FileDigest      = $FileDigest
  TimestampDigest = $TimestampDigest
  Append          = [bool]$Append
  DryRun          = [bool]$DryRun
  ContinueOnError = [bool]$ContinueOnError
}

$targets = Resolve-Targets -roots $Path -recurse:$Recurse -filterCsv $Filter
if ($targets.Count -eq 0) { ErrorX "No files matched. Check -Path/-Filter/-Recurse." }

Log ("Signing {0} file(s)" -f $targets.Count)
if ($cfg.PfxPath)      { Log "Using PFX    : $($cfg.PfxPath)" }
elseif ($cfg.Thumbprint){ Log "Using SHA1   : $($cfg.Thumbprint)" }
elseif ($cfg.SubjectName){ Log "Using Subject: $($cfg.SubjectName)" }
Log "Timestamp URL   : $($cfg.TimestampUrl)"
Log "File digest     : $($cfg.FileDigest)"
Log "Timestamp digest: $($cfg.TimestampDigest)"
if ($DryRun) { Warn "DRY-RUN enabled — no signatures will be applied." }

$failures = New-Object System.Collections.Generic.List[string]
foreach ($f in $targets) {
  Log "→ $f"
  $ok = Invoke-Sign -cfg $cfg -file $f
  if (-not $ok) { $failures.Add($f) | Out-Null; continue }
  if ($VerifyAfter -and -not $DryRun) {
    Invoke-Verify -file $f
  }
  if (-not $DryRun) {
    Write-Host ("SHA256 " + (Print-Hash -file $f)) -ForegroundColor DarkGray
  }
}

if ($failures.Count -gt 0) {
  $msg = "Signing failed for {0} file(s):`n{1}" -f $failures.Count, ($failures -join "`n")
  if ($ContinueOnError) {
    Warn $msg
  } else {
    ErrorX $msg
  }
} else {
  Log "All targets processed."
}

