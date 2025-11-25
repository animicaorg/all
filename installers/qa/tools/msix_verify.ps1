<#!
msix_verify.ps1 — Verify MSIX/APPX signature & manifest

Usage:
  pwsh installers/qa/tools/msix_verify.ps1 -Path .\Animica-Wallet.msix
  pwsh installers/qa/tools/msix_verify.ps1 -Path .\Animica-Wallet.msix -ExpectedPublisher "CN=Animica, O=Animica Inc., C=US"
  pwsh installers/qa/tools/msix_verify.ps1 -Path .\Animica-Wallet.msix -ExpectedName "io.animica.wallet" -ExpectedVersion "1.2.3.0" -RunSigntool
  pwsh installers/qa/tools/msix_verify.ps1 -Path .\Animica-Explorer.msixbundle -RunSigntool -SigntoolPath "C:\Program Files (x86)\Windows Kits\10\bin\x64\signtool.exe"

Exit codes:
  0  OK
  2  Signature invalid
  3  Publisher mismatch (manifest vs cert and/or expected)
  4  Manifest parse error
  5  File missing or unreadable
  6  Signtool reported failure
#>

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position=0)]
  [string]$Path,

  [string]$ExpectedPublisher,
  [string]$ExpectedName,
  [string]$ExpectedVersion,

  [switch]$RunSigntool,
  [string]$SigntoolPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Section($title) {
  Write-Host ''
  Write-Host "=== $title ===" -ForegroundColor Cyan
}

function Die($code, $msg) {
  Write-Error $msg
  exit $code
}

function Get-Hash([string]$p, [string]$alg = 'SHA256') {
  $h = Get-FileHash -Path $p -Algorithm $alg
  return @{
    algorithm = $alg
    value     = $h.Hash.ToLowerInvariant()
  }
}

function Read-ZipEntryText {
  param(
    [System.IO.Compression.ZipArchive]$Zip,
    [string]$EntryPath
  )
  $entry = $Zip.GetEntry($EntryPath)
  if (-not $entry) { return $null }
  $sr = New-Object System.IO.StreamReader($entry.Open())
  try {
    return $sr.ReadToEnd()
  } finally {
    $sr.Dispose()
  }
}

function Parse-Appx-Manifest([xml]$xml) {
  $pkg = $xml.SelectSingleNode("/*[local-name()='Package']")
  if (-not $pkg) { return $null }

  $id = $xml.SelectSingleNode("/*[local-name()='Package']/*[local-name()='Identity']")
  if (-not $id) { return $null }

  $name = $id.GetAttribute("Name")
  $publisher = $id.GetAttribute("Publisher")
  $version = $id.GetAttribute("Version")
  $arch = $id.GetAttribute("ProcessorArchitecture")

  # Capabilities (best effort across namespaces)
  $capNodes = $xml.SelectNodes("/*[local-name()='Package']/*[local-name()='Capabilities']/*")
  $caps = @()
  foreach ($c in $capNodes) {
    $n = $c.GetAttribute("Name")
    if ([string]::IsNullOrEmpty($n)) { $n = $c.LocalName }
    $caps += $n
  }

  return @{
    kind        = "appx"
    name        = $name
    publisher   = $publisher
    version     = $version
    architecture= $arch
    capabilities= $caps
  }
}

function Parse-AppxBundle-Manifest([xml]$xml) {
  $b = $xml.SelectSingleNode("/*[local-name()='Bundle']")
  if (-not $b) { return $null }

  $id = $xml.SelectSingleNode("/*[local-name()='Bundle']/*[local-name()='Identity']")
  if (-not $id) { return $null }

  $name = $id.GetAttribute("Name")
  $publisher = $id.GetAttribute("Publisher")
  $version = $id.GetAttribute("Version")

  # List contained packages (name/arch/version)
  $pkgNodes = $xml.SelectNodes("/*[local-name()='Bundle']/*[local-name()='Packages']/*[local-name()='Package']")
  $packages = @()
  foreach ($p in $pkgNodes) {
    $packages += @{
      file      = $p.GetAttribute("FileName")
      arch      = $p.GetAttribute("ProcessorArchitecture")
      version   = $p.GetAttribute("Version")
      resource  = $p.GetAttribute("ResourceId")
      type      = $p.GetAttribute("Type")
    }
  }

  return @{
    kind      = "bundle"
    name      = $name
    publisher = $publisher
    version   = $version
    packages  = $packages
  }
}

function Normalize-DN([string]$dn) {
  # Basic normalization (trim whitespace, case-insensitive). We intentionally DO NOT
  # reorder RDNs; MSIX requires exact string match between manifest Publisher and cert Subject.
  return ($dn -replace '\s+', '') .ToUpperInvariant()
}

function Verify-Signature([string]$p) {
  $sig = Get-AuthenticodeSignature -FilePath $p
  $subj = $null; $issuer = $null; $nb = $null; $na = $null; $thumb = $null
  $ts = $null; $tsIssuer = $null
  if ($sig.SignerCertificate) {
    $subj = $sig.SignerCertificate.Subject
    $issuer = $sig.SignerCertificate.Issuer
    $nb = $sig.SignerCertificate.NotBefore
    $na = $sig.SignerCertificate.NotAfter
    $thumb = $sig.SignerCertificate.Thumbprint
  }
  if ($sig.TimeStamperCertificate) {
    $ts = $sig.TimeStamperCertificate.Subject
    $tsIssuer = $sig.TimeStamperCertificate.Issuer
  }
  return @{
    status      = $sig.Status.ToString()
    statusMsg   = $sig.StatusMessage
    subject     = $subj
    issuer      = $issuer
    notBefore   = $nb
    notAfter    = $na
    thumbprint  = $thumb
    timestamped = [bool]($sig.TimeStamperCertificate)
    tsSubject   = $ts
    tsIssuer    = $tsIssuer
  }
}

function Run-Signtool([string]$p, [string]$signtoolPath) {
  $exe = $signtoolPath
  if ([string]::IsNullOrWhiteSpace($exe)) {
    $st = Get-Command signtool -ErrorAction SilentlyContinue
    if ($st) { $exe = $st.Source }
  }
  if ([string]::IsNullOrWhiteSpace($exe) -or -not (Test-Path $exe)) {
    return @{
      ran   = $false
      ok    = $null
      code  = $null
      output= "signtool not found"
    }
  }
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $exe
  $psi.Arguments = "verify /pa /v `"$p`""
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.UseShellExecute = $false
  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo = $psi
  [void]$proc.Start()
  $out = $proc.StandardOutput.ReadToEnd() + "`n" + $proc.StandardError.ReadToEnd()
  $proc.WaitForExit()
  return @{
    ran    = $true
    ok     = ($proc.ExitCode -eq 0)
    code   = $proc.ExitCode
    output = $out
  }
}

# ---- Main ----

if (-not (Test-Path -LiteralPath $Path)) {
  Die 5 "File not found: $Path"
}

$ext = [System.IO.Path]::GetExtension($Path).ToLowerInvariant()
$validExt = @('.msix', '.appx', '.msixbundle', '.appxbundle')
if ($validExt -notcontains $ext) {
  Write-Warning "Unexpected extension '$ext' (expected: $($validExt -join ', ')). Continuing."
}

Write-Section "File & Hash"
$hash = Get-Hash -p $Path -alg 'SHA256'
$len = (Get-Item -LiteralPath $Path).Length
"{0}`n  size: {1:N0} bytes`n  sha256: {2}" -f $Path, $len, $hash.value | Write-Host

Write-Section "Signature (Get-AuthenticodeSignature)"
$sig = Verify-Signature -p $Path
$sig | Format-List | Out-String | Write-Host

if ($sig.status -ne 'Valid') {
  Die 2 "Signature status is '$($sig.status)': $($sig.statusMsg)"
}

if ($RunSigntool) {
  Write-Section "Signtool Verify (/pa /v)"
  $st = Run-Signtool -p $Path -signtoolPath $SigntoolPath
  if ($st.ran) {
    Write-Host ($st.output.Trim())
    if (-not $st.ok) { Die 6 "signtool verify failed (exit $($st.code))." }
  } else {
    Write-Warning $st.output
  }
}

Write-Section "Manifest"
Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null
$fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
try {
  $zip = New-Object System.IO.Compression.ZipArchive($fs, [System.IO.Compression.ZipArchiveMode]::Read, $false)

  $xmlText = Read-ZipEntryText -Zip $zip -EntryPath 'AppxManifest.xml'
  $bundleXmlText = $null
  if (-not $xmlText) {
    $bundleXmlText = Read-ZipEntryText -Zip $zip -EntryPath 'AppxMetadata/AppxBundleManifest.xml'
  }

  $manifestInfo = $null
  if ($xmlText) {
    try {
      $xml = [xml]$xmlText
    } catch {
      Die 4 "Failed to parse AppxManifest.xml: $($_.Exception.Message)"
    }
    $manifestInfo = Parse-Appx-Manifest $xml
  } elseif ($bundleXmlText) {
    try {
      $xml = [xml]$bundleXmlText
    } catch {
      Die 4 "Failed to parse AppxBundleManifest.xml: $($_.Exception.Message)"
    }
    $manifestInfo = Parse-AppxBundle-Manifest $xml
  } else {
    Die 4 "Neither AppxManifest.xml nor AppxBundleManifest.xml found in archive."
  }

  $manifestInfo | Format-List | Out-String | Write-Host

  # Cross-check publisher vs certificate subject (exact DN string per MSIX rules)
  $pubOk = $false
  if ($manifestInfo.publisher -and $sig.subject) {
    $pubOk = ( (Normalize-DN $manifestInfo.publisher) -eq (Normalize-DN $sig.subject) )
  }
  if (-not $pubOk) {
    Write-Error ("Publisher mismatch:`n  Manifest: {0}`n  Cert:     {1}" -f $manifestInfo.publisher, $sig.subject)
    Die 3 "Publisher in manifest does not match signing certificate subject."
  } else {
    Write-Host "Publisher matches certificate subject ✅" -ForegroundColor Green
  }

  if ($ExpectedPublisher) {
    if ((Normalize-DN $ExpectedPublisher) -ne (Normalize-DN $manifestInfo.publisher)) {
      Die 3 "Manifest publisher does not match ExpectedPublisher."
    } else {
      Write-Host "ExpectedPublisher check passed ✅" -ForegroundColor Green
    }
  }
  if ($ExpectedName) {
    if ($ExpectedName -ne $manifestInfo.name) {
      Die 3 "Manifest Name '$($manifestInfo.name)' does not match ExpectedName '$ExpectedName'."
    } else {
      Write-Host "ExpectedName check passed ✅" -ForegroundColor Green
    }
  }
  if ($ExpectedVersion) {
    if ($ExpectedVersion -ne $manifestInfo.version) {
      Die 3 "Manifest Version '$($manifestInfo.version)' does not match ExpectedVersion '$ExpectedVersion'."
    } else {
      Write-Host "ExpectedVersion check passed ✅" -ForegroundColor Green
    }
  }

  # Summary JSON to STDOUT (for CI parsers)
  $summary = [ordered]@{
    path      = (Resolve-Path -LiteralPath $Path).Path
    size      = $len
    sha256    = $hash.value
    signature = $sig
    manifest  = $manifestInfo
    checks    = @{
      publisherMatchesCert = $pubOk
      expectedPublisherOk  = [bool]$ExpectedPublisher
      expectedNameOk       = [bool]$ExpectedName
      expectedVersionOk    = [bool]$ExpectedVersion
    }
  }
  $json = $summary | ConvertTo-Json -Depth 6
  Write-Section "Summary (JSON)"
  Write-Host $json

} finally {
  if ($zip) { $zip.Dispose() }
  if ($fs) { $fs.Dispose() }
}

exit 0
