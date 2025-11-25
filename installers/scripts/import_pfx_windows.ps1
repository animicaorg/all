<#!
--------------------------------------------------------------------------------
import_pfx_windows.ps1 — Import a Code Signing certificate (.pfx) on Windows

Usage:
  # Default action is 'setup'
  pwsh -File installers/scripts/import_pfx_windows.ps1
  powershell -ExecutionPolicy Bypass -File installers/scripts/import_pfx_windows.ps1

  # Explicit:
  pwsh -File installers/scripts/import_pfx_windows.ps1 setup
  pwsh -File installers/scripts/import_pfx_windows.ps1 cleanup

Inputs (from environment variables):
  PFX_BASE64                Base64-encoded PFX payload (optional; alternative to PFX_PATH)
  PFX_PATH                  Path to .pfx file on disk (optional if PFX_BASE64 is used)
  PFX_PASSWORD              Password for the PFX (can be empty)

  CERT_STORE_SCOPE          "CurrentUser" (default) or "LocalMachine"
  CERT_STORE_NAME           Target store name (default: "My" — the Personal store)

  # Optional chain (intermediate/root), PEM or DER; multiple certs allowed.
  IMPORT_CHAIN_BASE64       Base64-encoded .cer/.pem bundle
  CHAIN_CER_PATH            Path to .cer/.pem bundle on disk

  # cleanup mode:
  CERT_THUMBPRINT           Thumbprint to remove (if not provided, uses CODE_SIGN_CERT_THUMBPRINT)

Outputs (exports / GitHub Actions friendly):
  CODE_SIGN_CERT_THUMBPRINT Thumbprint of imported cert
  CODE_SIGN_CERT_SUBJECT    Subject (CN) of imported cert
  CODE_SIGN_CERT_STORE      Canonical Cert:\ path of the store used

Notes:
  - Works in Windows PowerShell 5.1 and PowerShell 7+.
  - For LocalMachine store, you may need elevated rights. The script will warn if not admin.
  - Private key ACLs are typically fine for CurrentUser. LocalMachine may need extra ACL work;
    this script attempts to set read access for the current user when feasible.
--------------------------------------------------------------------------------
!#>

param(
  [ValidateSet('setup','cleanup')]
  [string]$Action = 'setup'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Log   { param([string]$m) Write-Host "[INFO ] $m" -ForegroundColor Cyan }
function Write-Warn  { param([string]$m) Write-Warning $m }
function Write-Err   { param([string]$m) Write-Host "[ERROR] $m" -ForegroundColor Red }

function Is-Admin {
  try {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
  } catch { return $false }
}

function Add-ToGithubEnv {
  param([string]$Key, [string]$Value)
  if ($env:GITHUB_ENV) {
    "$Key=$Value" | Out-File -FilePath $env:GITHUB_ENV -Encoding UTF8 -Append
  }
}

function Resolve-PfxSource {
  if ([string]::IsNullOrWhiteSpace($env:PFX_BASE64) -eq $false) {
    $tmp = [IO.Path]::GetTempFileName() -replace '\.tmp$', '.pfx'
    [IO.File]::WriteAllBytes($tmp, [Convert]::FromBase64String($env:PFX_BASE64))
    return $tmp, $true
  }
  elseif ([string]::IsNullOrWhiteSpace($env:PFX_PATH) -eq $false) {
    if (-not (Test-Path -LiteralPath $env:PFX_PATH)) {
      throw "PFX_PATH not found: $($env:PFX_PATH)"
    }
    return $env:PFX_PATH, $false
  }
  else {
    throw "Provide PFX_BASE64 or PFX_PATH."
  }
}

function Resolve-ChainSource {
  if ([string]::IsNullOrWhiteSpace($env:IMPORT_CHAIN_BASE64) -eq $false) {
    $tmp = [IO.Path]::GetTempFileName() -replace '\.tmp$', '.cer'
    [IO.File]::WriteAllBytes($tmp, [Convert]::FromBase64String($env:IMPORT_CHAIN_BASE64))
    return $tmp, $true
  }
  elseif ([string]::IsNullOrWhiteSpace($env:CHAIN_CER_PATH) -eq $false) {
    if (-not (Test-Path -LiteralPath $env:CHAIN_CER_PATH)) {
      throw "CHAIN_CER_PATH not found: $($env:CHAIN_CER_PATH)"
    }
    return $env:CHAIN_CER_PATH, $false
  }
  else {
    return $null, $false
  }
}

function Ensure-StorePath {
  param([string]$Scope, [string]$Store)
  $path = "Cert:\$Scope\$Store"
  if (-not (Test-Path -LiteralPath $path)) {
    # Create store if missing
    New-Item -Path "Cert:\$Scope" -Name $Store -Force | Out-Null
  }
  return $path
}

function Import-Chain {
  param([string]$ChainPath)
  # Import into Intermediate Certification Authorities ("CA") store under the chosen scope.
  # For PEM with multiple certs, certutil handles the bundle.
  $scope = $env:CERT_STORE_SCOPE
  if ([string]::IsNullOrWhiteSpace($scope)) { $scope = 'CurrentUser' }

  $store = if ($scope -eq 'LocalMachine') { 'CA' } else { 'CA' }
  Write-Log "Importing chain to $scope\$store via certutil"
  $argScope = if ($scope -eq 'LocalMachine') { '-enterprise -f -user -addstore' } else { '-f -user -addstore' }

  # Use certutil; it's robust with PEM/DER and multiple certs
  $proc = Start-Process -FilePath certutil.exe -ArgumentList @($argScope, $store, $ChainPath) `
          -NoNewWindow -PassThru -Wait
  if ($proc.ExitCode -ne 0) {
    Write-Warn "certutil returned code $($proc.ExitCode); chain import may have partially succeeded."
  }
}

function Try-Set-PrivateKeyAcl {
  param([System.Security.Cryptography.X509Certificates.X509Certificate2]$Cert)
  try {
    if ($null -eq $Cert.PrivateKey -and $null -eq $Cert.GetType().GetProperty('PrivateKey').GetValue($Cert)) {
      return
    }
  } catch { }

  # CAPI keys (RSACryptoServiceProvider)
  try {
    $capi = $Cert.PrivateKey
    if ($capi -and $capi.CspKeyContainerInfo -and -not $capi.CspKeyContainerInfo.MachineKeyStore) {
      # CurrentUser keys don't need ACL change
      return
    }
  } catch { }

  # CNG keys path resolution
  try {
    $k = [System.Security.Cryptography.X509Certificates.RSACng]$Cert.GetRSAPrivateKey()
    if ($k -ne $null) {
      $keyName = $k.Key.UniqueName
      $keyDir  = "$env:ProgramData\Microsoft\Crypto\Keys"
      $keyPath = Join-Path $keyDir $keyName
      if (Test-Path -LiteralPath $keyPath) {
        $acl = Get-Acl -LiteralPath $keyPath
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($env:USERNAME, 'Read', 'Allow')
        $acl.SetAccessRule($rule)
        Set-Acl -LiteralPath $keyPath -AclObject $acl
        Write-Log "Granted read access on private key: $keyPath"
      }
    }
  } catch {
    Write-Warn "Could not adjust ACL on private key: $($_.Exception.Message)"
  }
}

function Setup {
  $scope = if ($env:CERT_STORE_SCOPE) { $env:CERT_STORE_SCOPE } else { 'CurrentUser' }
  $store = if ($env:CERT_STORE_NAME)  { $env:CERT_STORE_NAME }  else { 'My' }

  if ($scope -eq 'LocalMachine' -and -not (Is-Admin)) {
    Write-Warn "Importing into LocalMachine requires Administrator; consider using CurrentUser."
  }

  $storePath = Ensure-StorePath -Scope $scope -Store $store
  Write-Log "Using store: $storePath"

  $pfxPath = $null; $pfxTmp = $false
  $chainPath = $null; $chainTmp = $false

  try {
    $result = Resolve-PfxSource
    $pfxPath = $result[0]; $pfxTmp = $result[1]

    $chainRes = Resolve-ChainSource
    if ($chainRes -ne $null) {
      $chainPath = $chainRes[0]; $chainTmp = $chainRes[1]
    }

    $secure = ConvertTo-SecureString ($env:PFX_PASSWORD | ForEach-Object { $_ }) -AsPlainText -Force

    Write-Log "Importing PFX into $storePath"
    $import = Import-PfxCertificate -FilePath $pfxPath -Password $secure -CertStoreLocation $storePath -Exportable:$false

    if (-not $import) {
      throw "Import-PfxCertificate returned no certificate; check PFX/password."
    }

    # Use the first returned cert with private key
    $cert = $import | Where-Object { $_.HasPrivateKey } | Select-Object -First 1
    if (-not $cert) { $cert = $import | Select-Object -First 1 }

    Write-Log ("Imported: {0}  Thumbprint={1}" -f $cert.Subject, $cert.Thumbprint)

    if ($chainPath) { Import-Chain -ChainPath $chainPath }

    if ($scope -eq 'LocalMachine') {
      Try-Set-PrivateKeyAcl -Cert $cert
    }

    # Exports / CI env
    $env:CODE_SIGN_CERT_THUMBPRINT = $cert.Thumbprint
    $env:CODE_SIGN_CERT_SUBJECT    = $cert.Subject
    $env:CODE_SIGN_CERT_STORE      = $storePath

    Add-ToGithubEnv -Key 'CODE_SIGN_CERT_THUMBPRINT' -Value $cert.Thumbprint
    Add-ToGithubEnv -Key 'CODE_SIGN_CERT_SUBJECT'    -Value $cert.Subject
    Add-ToGithubEnv -Key 'CODE_SIGN_CERT_STORE'      -Value $storePath

    Write-Log "Thumbprint exported as CODE_SIGN_CERT_THUMBPRINT=$($cert.Thumbprint)"
  }
  finally {
    if ($pfxTmp -and $pfxPath -and (Test-Path -LiteralPath $pfxPath)) { Remove-Item -LiteralPath $pfxPath -Force -ErrorAction SilentlyContinue }
    if ($chainTmp -and $chainPath -and (Test-Path -LiteralPath $chainPath)) { Remove-Item -LiteralPath $chainPath -Force -ErrorAction SilentlyContinue }
  }
}

function Cleanup {
  $scope = if ($env:CERT_STORE_SCOPE) { $env:CERT_STORE_SCOPE } else { 'CurrentUser' }
  $store = if ($env:CERT_STORE_NAME)  { $env:CERT_STORE_NAME }  else { 'My' }
  $storePath = "Cert:\$scope\$store"

  $thumb = $env:CERT_THUMBPRINT
  if ([string]::IsNullOrWhiteSpace($thumb)) { $thumb = $env:CODE_SIGN_CERT_THUMBPRINT }

  if ([string]::IsNullOrWhiteSpace($thumb)) {
    throw "Provide CERT_THUMBPRINT (or ensure CODE_SIGN_CERT_THUMBPRINT is set)."
  }

  $item = Join-Path $storePath $thumb
  if (Test-Path -LiteralPath $item) {
    Write-Log "Removing certificate $thumb from $storePath"
    Remove-Item -LiteralPath $item -Force
    Write-Log "Removed."
  } else {
    Write-Warn "Certificate $thumb not found in $storePath"
  }
}

try {
  if ($Action -eq 'setup') { Setup }
  else { Cleanup }
} catch {
  Write-Err $_.Exception.Message
  exit 1
}
