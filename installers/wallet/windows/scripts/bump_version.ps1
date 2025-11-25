<#
  Animica Wallet — bump_version.ps1
  Updates the 4-part MSIX Version in an AppxManifest.xml, and (optionally) a .appinstaller feed.

  Examples:
    # Set explicit version on manifest only
    pwsh installers/wallet/windows/scripts/bump_version.ps1 `
      -ManifestPath dist/windows/stable/msix-layout/AppxManifest.xml `
      -Version 1.2.3.0

    # Increment build (fourth) component, also update appinstaller
    pwsh installers/wallet/windows/scripts/bump_version.ps1 `
      -ManifestPath dist/windows/stable/msix-layout/AppxManifest.xml `
      -AppInstallerPath dist/windows/stable/Animica-Wallet.appinstaller `
      -Increment build

  Notes:
    - Works on real XML manifests. Template files like Package.appxmanifest.tmpl
      contain placeholders (e.g., {{VERSION}}) and are not suitable for this script.
#>

[CmdletBinding(PositionalBinding = $false)]
param(
  [Parameter(Mandatory = $true)]
  [ValidateScript({ Test-Path $_ })]
  [string]$ManifestPath,

  [ValidateScript({ Test-Path $_ })]
  [string]$AppInstallerPath,

  [ValidatePattern('^\d+\.\d+\.\d+\.\d+$')]
  [string]$Version,                         # If provided, sets exact 4-part version

  [ValidateSet('major','minor','patch','build')]
  [string]$Increment,                       # If provided (and -Version not given), increments that part

  [switch]$DryRun                           # Show changes without writing files
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Log([string]$m)  { Write-Host "[bump] $m" -ForegroundColor Cyan }
function Warn([string]$m) { Write-Host "[warn] $m" -ForegroundColor Yellow }
function Fail([string]$m) { Write-Host "[fail] $m" -ForegroundColor Red; throw $m }

function Read-Xml([string]$path) {
  try {
    $xml = New-Object System.Xml.XmlDocument
    $xml.PreserveWhitespace = $true
    $xml.Load($path)
    return $xml
  } catch {
    Fail "Failed to load XML: $path — $($_.Exception.Message)"
  }
}

function Get-ManifestVersion([xml]$xml) {
  # Appx manifest typically: <Package xmlns="..."><Identity Version="x.y.z.b" .../>
  $pkg = $xml.DocumentElement
  if (-not $pkg) { Fail "No root element in manifest." }
  $id = $pkg.GetElementsByTagName("Identity")
  if ($id.Count -eq 0) { Fail "No <Identity> in manifest." }
  return $id.Item(0).GetAttribute("Version")
}

function Set-ManifestVersion([xml]$xml, [string]$newVersion) {
  $pkg = $xml.DocumentElement
  $id = $pkg.GetElementsByTagName("Identity").Item(0)
  $null = $id.SetAttribute("Version", $newVersion)
}

function Parse-Version([string]$ver) {
  $parts = $ver.Split('.').ForEach({ [int]$_ })
  if ($parts.Count -ne 4) { Fail "Version must have four integer parts: $ver" }
  return ,$parts
}

function Bump-Version([string]$current, [string]$which) {
  $p = Parse-Version $current
  switch ($which) {
    'major' { $p[0]++; $p[1]=0; $p[2]=0; $p[3]=0 }
    'minor' { $p[1]++; $p[2]=0; $p[3]=0 }
    'patch' { $p[2]++; $p[3]=0 }
    'build' { $p[3]++ }
    default { Fail "Unknown increment: $which" }
  }
  return "{0}.{1}.{2}.{3}" -f $p[0],$p[1],$p[2],$p[3]
}

function Set-AppInstallerVersion([xml]$xml, [string]$newVersion) {
  # Default namespace makes property access tricky; set attributes directly.
  $root = $xml.DocumentElement
  if (-not $root) { Fail "No root <AppInstaller> in appinstaller XML." }
  $null = $root.SetAttribute("Version", $newVersion)

  # Update nested <MainPackage Version="...">
  $mp = $root.GetElementsByTagName("MainPackage")
  if ($mp.Count -gt 0) {
    $null = $mp.Item(0).SetAttribute("Version", $newVersion)
  } else {
    Warn "No <MainPackage> element found in appinstaller; only root Version updated."
  }
}

# Load manifest and compute new version
$manifestXml = Read-Xml $ManifestPath
$current = Get-ManifestVersion $manifestXml
Log "Current manifest version: $current"

if ($Version) {
  $newVer = $Version
} elseif ($Increment) {
  $newVer = Bump-Version -current $current -which $Increment
} else {
  Fail "Provide either -Version <x.y.z.b> or -Increment {major|minor|patch|build}."
}

if ($newVer -eq $current) {
  Warn "New version equals current ($current). Nothing to do."
  exit 0
}

Log "New version → $newVer"

if ($DryRun) {
  Log "Dry-run enabled; not writing changes."
  if ($AppInstallerPath) { Log "Would also set appinstaller version to $newVer at: $AppInstallerPath" }
  exit 0
}

# Update manifest
Set-ManifestVersion -xml $manifestXml -newVersion $newVer
$manifestXml.Save($ManifestPath)
Log "Wrote manifest: $ManifestPath"

# Optionally update appinstaller
if ($AppInstallerPath) {
  if (-not (Test-Path $AppInstallerPath)) {
    Fail "AppInstallerPath not found: $AppInstallerPath"
  }
  $aiXml = Read-Xml $AppInstallerPath
  Set-AppInstallerVersion -xml $aiXml -newVersion $newVer
  $aiXml.Save($AppInstallerPath)
  Log "Wrote appinstaller: $AppInstallerPath"
}

Log "Done ✔"
