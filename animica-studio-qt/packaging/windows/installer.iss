; Inno Setup script for Animica Studio Qt (Windows installer).
; Built by .github/workflows/studio-qt-windows.yml on a native windows-latest
; runner, AFTER PyInstaller produces dist/AnimicaStudioQt/.
;
; Paths are relative to THIS file (animica-studio-qt/packaging/windows/), so:
;   ..\..\dist\AnimicaStudioQt  = the PyInstaller onedir output
;   ..\..                       = the animica-studio-qt/ dir (installer lands here)

#define MyAppName "Animica Studio Qt"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Animica"
#define MyAppURL "https://animica.org"
#define MyAppExeName "AnimicaStudioQt.exe"

[Setup]
AppId={{7F2A6B1C-9E44-4D2B-AE7C-STUDIOQT0001}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/studio
DefaultDirName={autopf}\AnimicaStudioQt
DefaultGroupName=Animica Studio Qt
DisableProgramGroupPage=yes
OutputDir=..\..
OutputBaseFilename=animica-studio-qt-windows-x64-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\AnimicaStudioQt\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
