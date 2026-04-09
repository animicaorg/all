#ifndef StageDir
  #error "StageDir must be provided to ISCC via /DStageDir=<path>"
#endif

#ifndef OutputDir
  #error "OutputDir must be provided to ISCC via /DOutputDir=<path>"
#endif

#ifndef OutputBaseFilename
  #define OutputBaseFilename "AnimicaWallet-Setup"
#endif

#ifndef AppVersion
  #error "AppVersion must be provided to ISCC via /DAppVersion=<version>"
#endif

#ifndef VersionInfoVersion
  #define VersionInfoVersion AppVersion
#endif

#define AppName "Animica Wallet"
#define AppPublisher "Animica"
#define AppExecutable "animica-wallet.exe"
#define AppId "org.animica.wallet"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
WizardStyle=modern
Compression=lzma
SolidCompression=yes
ChangesAssociations=no
ChangesEnvironment=no
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExecutable}
VersionInfoVersion={#VersionInfoVersion}
VersionInfoProductName={#AppName}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Installer
SetupLogging=yes
#ifdef InstallerIconFile
SetupIconFile={#InstallerIconFile}
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExecutable}"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExecutable}"; Tasks: desktopicon; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#AppExecutable}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
