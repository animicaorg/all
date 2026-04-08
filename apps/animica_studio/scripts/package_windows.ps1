# Package Animica Studio for Windows as a packaged .exe.
# Usage: pwsh -File scripts/package_windows.ps1 [--skip-build]

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir ".."))
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $AppRoot "../.."))

if ($env:PYTHON) {
    $PythonCmd = @($env:PYTHON)
} elseif (Test-Path (Join-Path $RepoRoot ".venv/Scripts/python.exe")) {
    $PythonCmd = @((Join-Path $RepoRoot ".venv/Scripts/python.exe"))
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonCmd = @("py", "-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonCmd = @((Get-Command python).Source)
} else {
    throw "Python 3.11+ is required to package Animica Studio."
}

$PythonArgs = @($PythonCmd | Select-Object -Skip 1)

& $PythonCmd[0] @PythonArgs (Join-Path $ScriptDir "package_release.py") windows @args
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
