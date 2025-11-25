<#
  Animica Wallet — Windows Release Builder
  Builds Flutter (Windows), packages MSIX (preferred), optionally NSIS; signs artifacts and emits an .appinstaller feed.

  Usage (PowerShell):
    pwsh installers/wallet/windows/scripts/build_release.ps1 `
      -Version 1.2.3.0 -Channel stable -Publisher "CN=Animica Labs, Inc." `
      -FeedBase "https://updates.animica.dev/wallet/windows/stable/"

  Parameters:
    -Version      4-part version required by MSIX (e.g., 1.2.3.0)
    -Channel      stable | beta (used in output paths)
    -AppName      Display/app name (default: "Animica Wallet")
    -BundleId     Identity/Name in MSIX (default: "AnimicaWallet")
    -Publisher    Must match code signing cert subject (CN=...)
    -Arch         x64 | arm64 (default: x64)
    -SkipBuild    Reuse existing Flutter build
    -NSIS         Also produce NSIS setup if installer.nsi exists
    -FeedBase     HTTPS base URL where artifacts are hosted (for .appinstaller)

  Signing:
    - Expects a code signing cert imported (user/machine store) or a PFX supplied via env:
        CSC_PFX_PATH, CSC_PFX_PASSWORD
      Alternatively specify a subject name via:
        CSC_SUBJECT_NAME
    - Timestamps via DigiCert TSA.

  Outputs:
    dist/windows/<channel>/
      Animica-Wallet_<Version>_<Arch>.msix
      Animica-Wallet.appinstaller        (if -FeedBase provided)
      Animica-Wallet-Setup-<Version>.exe (if -NSIS and NSIS script present)
#>

[CmdletBinding(PositionalBinding = $false)]
param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern('^\d+\.\d+\.\d+\.\d+$')]
  [string]$Version,

  [ValidateSet('stable','beta')]
  [string]$Channel = 'stable',

  [string]$AppName  = 'Animica Wallet',
  [string]$BundleId = 'AnimicaWallet',

  [Parameter(Mandatory = $true)]
  [string]$Publisher,  # e.g., "CN=Animica Labs, Inc."

  [ValidateSet('x64','arm64')]
  [string]$Arch = 'x64',

  [switch]$SkipBuild,
  [switch]$NSIS,

  [string]$FeedBase  # e.g., https://updates.animica.dev/wallet/windows/stable/
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Log([string]$msg)   { Write-Host "[build] $msg" -ForegroundColor Cyan }
function Warn([string]$msg)  { Write-Host "[warn ] $msg" -ForegroundColor Yellow }
function Fail([string]$msg)  { Write-Host "[fail ] $msg" -ForegroundColor Red; throw $msg }

function Need-Tool([string]$exe, [string]$hint) {
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
    Fail "Missing tool '$exe'. $hint"
  }
}

function Render-Template([string]$TemplatePath, [hashtable]$Vars, [string]$OutputPath) {
  if (-not (Test-Path $TemplatePath)) { Fail "Template not found: $TemplatePath" }
  $content = Get-Content -LiteralPath $TemplatePath -Raw -Encoding UTF8
  foreach ($k in $Vars.Keys) {
    $needle = '{{' + $k + '}}'
    $content = $content -replace [Regex]::Escape($needle), [string]$Vars[$k]
  }
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
  Set-Content -LiteralPath $OutputPath -Value $content -NoNewline -Encoding UTF8
}

function Sign-File([string]$PathToSign) {
  Need-Tool 'signtool.exe' 'Install Windows SDK (signtool).'
  $tsa = 'https://timestamp.digicert.com'
  $sigArgs = @('sign', '/fd', 'sha256', '/tr', $tsa, '/td', 'sha256')

  if ($env:CSC_PFX_PATH -and (Test-Path $env:CSC_PFX_PATH)) {
    Log "Signing with PFX: $($env:CSC_PFX_PATH)"
    $sigArgs += @('/f', $env:CSC_PFX_PATH)
    if ($env:CSC_PFX_PASSWORD) { $sigArgs += @('/p', $env:CSC_PFX_PASSWORD) }
  } elseif ($env:CSC_SUBJECT_NAME) {
    Log "Signing with subject name: $($env:CSC_SUBJECT_NAME)"
    $sigArgs += @('/n', $env:CSC_SUBJECT_NAME)
  } else {
    Warn "No explicit signing configuration found. Attempting default cert selection by subject: $Publisher"
    $sigArgs += @('/n', $Publisher)
  }

  $sigArgs += @($PathToSign)
  $p = Start-Process -FilePath 'signtool.exe' -ArgumentList $sigArgs -Wait -PassThru -NoNewWindow
  if ($p.ExitCode -ne 0) { Fail "signtool failed for $PathToSign (exit $($p.ExitCode))" }
}

# Locate repo root (this script lives in installers/wallet/windows/scripts)
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..') | Select-Object -ExpandProperty Path
$DistRoot = Join-Path $RepoRoot "dist\windows\$Channel"
$OutNameBase = ($AppName -replace '\s+','-')
$MsixName = "${OutNameBase}_${Version}_${Arch}.msix"
$MsixPath = Join-Path $DistRoot $MsixName
$LayoutDir = Join-Path $DistRoot 'msix-layout'
$MsixTemplateDir = Join-Path $RepoRoot 'installers\wallet\windows\msix'
$ManifestTemplate = Join-Path $MsixTemplateDir 'Package.appxmanifest.tmpl'
$ManifestOut = Join-Path $LayoutDir 'AppxManifest.xml'

# Tooling checks
Need-Tool 'flutter'    'Install Flutter SDK and ensure it is on PATH.'
Need-Tool 'MakeAppx'   'Install Windows 10/11 SDK (MakeAppx).'
Need-Tool 'powershell' 'PowerShell is required.'

# 1) Build Flutter (unless skipped)
$RunnerExeRel = "build\windows\$Arch\runner\Release\$AppName.exe"
$RunnerExe = Join-Path $RepoRoot $RunnerExeRel

if (-not $SkipBuild) {
  Log "Building Flutter Windows ($Arch, release)…"
  Push-Location $RepoRoot
  try {
    flutter build windows --release | Write-Host
  } catch {
    Fail "Flutter build failed: $($_.Exception.Message)"
  } finally {
    Pop-Location
  }
} else {
  Log "Skipping Flutter build (using existing artifacts)."
}

if (-not (Test-Path $RunnerExe)) {
  Fail "Built app not found: $RunnerExe"
}

# 2) Prepare MSIX layout
Log "Preparing MSIX layout at: $LayoutDir"
New-Item -ItemType Directory -Force -Path $LayoutDir | Out-Null
# Copy the EXE and any required runtime files
Copy-Item -Force -Path $RunnerExe -Destination (Join-Path $LayoutDir "$AppName.exe")
# Optional: include dependencies (VCLibs etc.) if needed; add as required.

# Render AppxManifest.xml
Log "Rendering Appx manifest from template."
$manifestVars = @{
  'BUNDLE_ID'   = $BundleId
  'DISPLAY_NAME'= $AppName
  'PUBLISHER'   = $Publisher
  'VERSION'     = $Version
  'ARCH'        = $Arch
  'EXECUTABLE'  = "$AppName.exe"
}
Render-Template -TemplatePath $ManifestTemplate -Vars $manifestVars -OutputPath $ManifestOut

# 3) Pack MSIX
New-Item -ItemType Directory -Force -Path $DistRoot | Out-Null
if (Test-Path $MsixPath) { Remove-Item -Force $MsixPath }
Log "Packing MSIX → $MsixPath"
$packArgs = @('pack', '/d', $LayoutDir, '/p', $MsixPath, '/o')
$proc = Start-Process -FilePath 'MakeAppx' -ArgumentList $packArgs -Wait -PassThru -NoNewWindow
if ($proc.ExitCode -ne 0) { Fail "MakeAppx failed (exit $($proc.ExitCode))" }

# 4) Sign MSIX
Sign-File -PathToSign $MsixPath

# 5) Verify signature
Log "Verifying signature on MSIX…"
$verify = Start-Process -FilePath 'signtool.exe' -ArgumentList @('verify','/pa','/all','/v',$MsixPath) -Wait -PassThru -NoNewWindow
if ($verify.ExitCode -ne 0) { Fail "Signature verification failed for $MsixPath" }

# 6) Generate .appinstaller (if FeedBase provided)
if ($FeedBase) {
  if ($FeedBase -notmatch '^https://') {
    Warn "FeedBase should be HTTPS; got: $FeedBase"
  }
  $msixUrl = ($FeedBase.TrimEnd('/') + '/' + $MsixName)
  $AppInstallerPath = Join-Path $DistRoot "$OutNameBase.appinstaller"
  Log "Emitting App Installer feed → $AppInstallerPath"
  $appinstaller = @"
<?xml version="1.0" encoding="utf-8"?>
<AppInstaller Uri="$msixUrl"
              Version="$Version"
              xmlns="http://schemas.microsoft.com/appx/appinstaller/2017/2">
  <MainPackage Name="$BundleId"
               Publisher="$Publisher"
               Version="$Version"
               ProcessorArchitecture="$Arch"
               Uri="$msixUrl" />
  <UpdateSettings>
    <OnLaunch HoursBetweenUpdateChecks="24" />
    <AutomaticBackgroundTask />
    <ShowPrompt />
  </UpdateSettings>
</AppInstaller>
"@
  Set-Content -LiteralPath $AppInstallerPath -Value $appinstaller -Encoding UTF8
}

# 7) (Optional) NSIS packaging
if ($NSIS) {
  $NsisScript = Join-Path $RepoRoot 'installers\wallet\windows\nsis\installer.nsi'
  if (Test-Path $NsisScript) {
    Need-Tool 'makensis' 'Install NSIS and ensure makensis is on PATH.'
    $SetupName = "$OutNameBase-Setup-$($Version -replace '\.0$','').exe"
    $SetupPath = Join-Path $DistRoot $SetupName
    Log "Building NSIS installer → $SetupPath"
    $defines = @(
      "/DAPP_NAME=$AppName",
      "/DVERSION=$Version",
      "/DARCH=$Arch",
      "/DOUTPUT=$SetupPath",
      "/DBINARY=$(Join-Path $LayoutDir "$AppName.exe")"
    )
    $nsis = Start-Process -FilePath 'makensis' -ArgumentList @($defines + $NsisScript) -Wait -PassThru -NoNewWindow
    if ($nsis.ExitCode -ne 0) { Fail "makensis failed (exit $($nsis.ExitCode))" }

    Log "Signing NSIS installer…"
    Sign-File -PathToSign $SetupPath

    Log "Verifying NSIS signature…"
    $v2 = Start-Process -FilePath 'signtool.exe' -ArgumentList @('verify','/pa','/all','/v',$SetupPath) -Wait -PassThru -NoNewWindow
    if ($v2.ExitCode -ne 0) { Fail "Signature verification failed for $SetupPath" }
  } else {
    Warn "NSIS requested but script not found: $NsisScript — skipping."
  }
}

# 8) Hashes & metadata
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $MsixPath).Hash.ToLower()
$len  = (Get-Item -LiteralPath $MsixPath).Length
Log "MSIX ready:
  Path   : $MsixPath
  Size   : $len bytes
  SHA256 : $hash
  Arch   : $Arch
  Channel: $Channel
  Version: $Version"

Log "Done ✔"
