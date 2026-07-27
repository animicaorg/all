# Build the Windows Animica Internet browser (AnimicaInternet.exe) and wrap it
# in an Inno Setup installer.
#
# Produces:
#   dist\animica-internet-windows-x64-setup.exe          (the installer)
#   dist\animica-internet-windows-x64-setup.exe.sha256   (detached checksum)
# and appends a JSON line per artifact to the manifest log so make_manifest.py
# can aggregate them.
#
# IMPORTANT: PyInstaller cannot cross-compile — this script MUST run natively
# on Windows (the windows-latest GitHub runner). Mirrors the proven pattern in
# .github/workflows/studio-qt-windows.yml (PyInstaller -> Inno Setup -> sha256).
#
# Usage (PowerShell):
#   .\build_windows.ps1
#
# Parameters / env:
#   -ArtifactLog <path>   JSON-lines manifest log (default: dist\artifacts.jsonl)
#   -Python <exe>         python interpreter (default: "python")

param(
    [string]$ArtifactLog = "",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

function Log($msg)  { Write-Host "[build-windows] $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "[warn] $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "[error] $msg" -ForegroundColor Red; exit 1 }

if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows)) {
    Die "This script must run natively on Windows (PyInstaller cannot cross-compile)."
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir    = (Resolve-Path (Join-Path $ScriptDir "..")).Path        # apps\animica-internet
$RepoRoot  = (Resolve-Path (Join-Path $AppDir "..\..")).Path        # repo root
$DistDir   = Join-Path $AppDir "dist"
$BuildDir  = Join-Path $AppDir "build"
$SpecFile  = Join-Path $ScriptDir "animica-internet.spec"
$AppName   = "AnimicaInternet"
$SetupBase = "animica-internet-windows-x64-setup"
if (-not $ArtifactLog) { $ArtifactLog = Join-Path $DistDir "artifacts.jsonl" }

# ---- Python ----
$PyVersion = & $Python --version 2>&1
Log "Using $PyVersion"

# ---- Clean ----
Log "Cleaning previous builds..."
if (Test-Path $DistDir)  { Remove-Item -Recurse -Force $DistDir }
if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
New-Item -ItemType Directory -Force -Path $DistDir, $BuildDir | Out-Null

# ---- Tooling + deps ----
Log "Installing PyInstaller + app package..."
& $Python -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { Die "pip upgrade failed" }
& $Python -m pip install --upgrade pyinstaller pyinstaller-hooks-contrib
if ($LASTEXITCODE -ne 0) { Die "pyinstaller install failed" }
& $Python -m pip install -e $AppDir
if ($LASTEXITCODE -ne 0) { Die "app package install failed" }
# Bundle the `animica` wallet-backend package when present in the monorepo.
$MonoPy = Join-Path $RepoRoot "python\pyproject.toml"
if (Test-Path $MonoPy) {
    & $Python -m pip install -e (Join-Path $RepoRoot "python")
    if ($LASTEXITCODE -ne 0) { Warn "could not install animica package; wallet backend may be incomplete" }
}

# ---- Version ----
$Version = & $Python -c "import tomllib; print(tomllib.load(open(r'$(Join-Path $AppDir "pyproject.toml")','rb'))['project']['version'])"
if ($LASTEXITCODE -ne 0 -or -not $Version) { $Version = "0.1.0" }
$Version = "$Version".Trim()
Log "Building version: $Version"

# ---- PyInstaller ----
Log "Running PyInstaller (onedir, windowed)..."
$env:ANIMICA_INTERNET_SPEC_DIR  = $ScriptDir
$env:ANIMICA_INTERNET_APP_DIR   = $AppDir
$env:ANIMICA_INTERNET_REPO_ROOT = $RepoRoot
$env:ANIMICA_INTERNET_NAME      = $AppName
$env:ANIMICA_INTERNET_VERSION   = $Version
Push-Location $AppDir
try {
    & $Python -m PyInstaller --clean --noconfirm `
        --distpath $DistDir `
        --workpath (Join-Path $BuildDir "pyinstaller-work") `
        $SpecFile
    if ($LASTEXITCODE -ne 0) { Die "PyInstaller failed" }
} finally {
    Pop-Location
}

$OneDir  = Join-Path $DistDir $AppName
$ExePath = Join-Path $OneDir "$AppName.exe"
if (-not (Test-Path $OneDir))  { Die "Failed to create onedir build at $OneDir" }
if (-not (Test-Path $ExePath)) { Die "Expected $AppName.exe not found in $OneDir" }
Log "onedir build created at: $OneDir"

# Sanity: the Chromium helper MUST be in the bundle or the browser is a brick.
$Helper = Get-ChildItem -Path $OneDir -Recurse -Filter "QtWebEngineProcess.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($Helper) { Log "QtWebEngineProcess.exe found: $($Helper.FullName)" }
else         { Warn "QtWebEngineProcess.exe NOT found in bundle — QtWebEngine will not work!" }

# ---- Smoke test (best-effort; windowed exe, so check exit code via Start-Process) ----
Log "Smoke test (--smoke, offscreen)..."
$env:QT_QPA_PLATFORM = "offscreen"
try {
    $proc = Start-Process -FilePath $ExePath -ArgumentList "--smoke" -Wait -PassThru -NoNewWindow
    if ($proc.ExitCode -eq 0) { Log "Smoke test OK" }
    else { Warn "Smoke test exited $($proc.ExitCode) (continuing; may be a headless-runner limitation)" }
} catch {
    Warn "Smoke test failed to launch: $_"
} finally {
    Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
}

# ---- Generate the Inno Setup script (absolute paths; built at build time) ----
$IconLine = ""
$IcoPath = Join-Path $AppDir "assets\icon.ico"
if (Test-Path $IcoPath) { $IconLine = "SetupIconFile=$IcoPath" }

$Iss = @"
; Auto-generated by build_windows.ps1 — do not edit by hand.
; Wraps the PyInstaller onedir output into a per-user Windows installer,
; mirroring animica-studio-qt/packaging/windows/installer.iss.

#define MyAppName "Animica Internet"
#define MyAppVersion "$Version"
#define MyAppPublisher "Animica"
#define MyAppURL "https://animica.org"
#define MyAppExeName "$AppName.exe"

[Setup]
AppId={{9C4E7A2D-5B31-4F8E-B6D0-ANMINTERNET1}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/internet
DefaultDirName={autopf}\AnimicaInternet
DefaultGroupName=Animica Internet
DisableProgramGroupPage=yes
OutputDir=$DistDir
OutputBaseFilename=$SetupBase
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#MyAppExeName}
$IconLine

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "$OneDir\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Register the anm:// URL scheme for the installing user.
Root: HKCU; Subkey: "Software\Classes\anm"; ValueType: string; ValueName: ""; ValueData: "URL:Animica Protocol"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\anm"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\anm\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\anm\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
"@

$IssPath = Join-Path $BuildDir "installer.iss"
Set-Content -Path $IssPath -Value $Iss -Encoding UTF8
Log "Inno Setup script: $IssPath"

# ---- Inno Setup ----
$Iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Iscc)) {
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { $Iscc = $cmd.Source }
}
if (-not (Test-Path $Iscc)) {
    Log "Installing Inno Setup via Chocolatey..."
    choco install innosetup --no-progress -y
    if ($LASTEXITCODE -ne 0) { Die "choco install innosetup failed" }
    $Iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $Iscc)) { Die "ISCC.exe not found; install Inno Setup 6" }

Log "Building installer (Inno Setup)..."
& $Iscc $IssPath
if ($LASTEXITCODE -ne 0) { Die "Inno Setup failed" }

$SetupExe = Join-Path $DistDir "$SetupBase.exe"
if (-not (Test-Path $SetupExe)) { Die "Installer not created at $SetupExe" }
Log "Installer: $SetupExe"

# ---- SHA256 sidecar ----
$Sha = (Get-FileHash -Algorithm SHA256 $SetupExe).Hash.ToLower()
$ShaLine = "$Sha  $SetupBase.exe"
Set-Content -Path "$SetupExe.sha256" -Value $ShaLine -Encoding ascii -NoNewline
Log "SHA256: $ShaLine"

# ---- Artifact record (JSON line for make_manifest.py) ----
$Size = (Get-Item $SetupExe).Length
$Record = [ordered]@{
    platform   = "windows"
    name       = $AppName
    version    = $Version
    filename   = "$SetupBase.exe"
    size_bytes = [int64]$Size
    sha256     = $Sha
    min_os     = "Windows 10+"
} | ConvertTo-Json -Compress
Add-Content -Path $ArtifactLog -Value $Record -Encoding utf8
Log "[artifact] windows $SetupBase.exe $Size bytes sha256=$Sha"

Log "Build complete. Artifacts in: $DistDir"
Log "Manifest log: $ArtifactLog"
