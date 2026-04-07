# Package Animica Studio for Windows using PyInstaller
# Usage: pwsh -File scripts/package_windows.ps1
# Requirements: pip install pyinstaller

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot/..

Write-Host "==> Building version..."
python scripts/build_version.py animica_studio/_version.py

Write-Host "==> Running PyInstaller..."
pyinstaller `
    --clean `
    --noconfirm `
    scripts/pyinstaller.spec

Write-Host "==> Done. Artifact in dist/"
