param(
  [string]$Version = "0.2.0"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path "$PSScriptRoot/../../.."
$AppDir = Join-Path $Root "apps/aicf-provider"
$OutDir = Join-Path $Root "dist/provider/windows"
$BundleName = "aicf-provider-worker-$Version-windows-x64.zip"

Set-Location $AppDir
py -m pip install pyinstaller
py -m PyInstaller --onefile --name aicf-provider-worker worker.py

if (!(Test-Path $OutDir)) {
  New-Item -ItemType Directory -Path $OutDir | Out-Null
}

$stage = Join-Path $env:TEMP "aicf-provider-win-$Version"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage | Out-Null

Copy-Item "dist/aicf-provider-worker.exe" "$stage/aicf-provider-worker.exe"
Copy-Item "provider.config.example.json" "$stage/provider.config.example.json"
Copy-Item "scripts/start-worker.bat" "$stage/start-worker.bat"
Copy-Item "scripts/benchmark-worker.bat" "$stage/benchmark-worker.bat"

Compress-Archive -Path "$stage/*" -DestinationPath (Join-Path $OutDir $BundleName) -Force
Write-Host "Built $BundleName"
