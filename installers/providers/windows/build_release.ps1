param(
  [string]$Version = "0.2.0"
)

$ErrorActionPreference = "Stop"
& "$PSScriptRoot/../../../apps/aicf-provider/scripts/build_windows_bundle.ps1" -Version $Version
