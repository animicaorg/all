; -----------------------------------------------------------------------------
; Animica Explorer — NSIS Installer (Modern UI 2)
; Production-ready, parameterized NSIS script for packaging a Tauri Win32 app.
;
; USAGE (examples):
;   makensis ^
;     /DAPP_VERSION=0.1.0 ^
;     /DSOURCE_DIR="C:\path\to\staged\App" ^
;     /DOUTFILE="dist\Animica-Explorer-Setup-0.1.0.exe" ^
;     /DWEBVIEW2_BOOTSTRAPPER="C:\path\to\MicrosoftEdgeWebview2Setup.exe" ^
;     installers\explorer-desktop\windows\nsis\installer.nsi
;
; NOTES:
; - SOURCE_DIR must contain the app EXE and any required DLLs/resources.
; - MAIN_EXE should be the primary executable's filename (default: animica-explorer.exe).
; - WebView2 bootstrapper is optional; if provided, we will run it silently.
; - This script installs for "All Users" (admin required) into Program Files.
; -----------------------------------------------------------------------------

!define MUI_ABORTWARNING
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"

; ------------------------ Configuration (overridable via /D...) --------------
!ifndef APP_NAME
  !define APP_NAME "Animica Explorer"
!endif
!ifndef COMPANY_NAME
  !define COMPANY_NAME "Animica Labs"
!endif
!ifndef PUBLISHER_URL
  !define PUBLISHER_URL "https://animica.dev"
!endif
!ifndef APP_VERSION
  !define APP_VERSION "0.1.0"
!endif
!ifndef MAIN_EXE
  !define MAIN_EXE "animica-explorer.exe"
!endif
!ifndef SOURCE_DIR
  ; Provide a default "payload" next to this script if not overridden
  !define SOURCE_DIR ".\payload"
!endif
!ifndef OUTFILE
  !define OUTFILE "Animica-Explorer-Setup-${APP_VERSION}.exe"
!endif

; Optional: path to WebView2 bootstrapper EXE (silent install)
/*
Example bootstrapper:
https://go.microsoft.com/fwlink/p/?LinkId=2124703
*/
!ifdef WEBVIEW2_BOOTSTRAPPER
  !define HAVE_WEBVIEW2 1
!endif

; Registry uninstall key
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\AnimicaExplorer"

; Install dir defaults to Program Files (x64 when present)
!define INSTALL_DIR_NAME "Animica\Explorer"

; ------------------------ Installer meta --------------------------------------
Unicode true
RequestExecutionLevel admin
Name "${APP_NAME}"
OutFile "${OUTFILE}"
BrandingText "Animica — ${APP_NAME} ${APP_VERSION}"

; Use correct Program Files for architecture
Var INSTDIR_ARCH_ROOT
Function .onInit
  ${If} ${RunningX64}
    StrCpy $INSTDIR_ARCH_ROOT "$PROGRAMFILES64"
    SetRegView 64
  ${Else}
    StrCpy $INSTDIR_ARCH_ROOT "$PROGRAMFILES"
    SetRegView 32
  ${EndIf}
FunctionEnd

InstallDir "$INSTDIR_ARCH_ROOT\${INSTALL_DIR_NAME}"
InstallDirRegKey HKLM "${UNINST_KEY}" "InstallLocation"

; ------------------------ UI Pages -------------------------------------------
!define MUI_ICON "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
!define MUI_UNICON "${NSISDIR}\Contrib\Graphics\Icons\modern-uninstall.ico"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "${NSISDIR}\Contrib\Graphics\Header\nsis3-mui2.bmp"

!ifdef LICENSE_FILE
  !insertmacro MUI_PAGE_WELCOME
  !insertmacro MUI_PAGE_LICENSE "${LICENSE_FILE}"
!else
  !insertmacro MUI_PAGE_WELCOME
!endif

!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES

!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION "RunAnimicaExplorer"
!define MUI_FINISHPAGE_LINK "Visit ${COMPANY_NAME}"
!define MUI_FINISHPAGE_LINK_LOCATION "${PUBLISHER_URL}"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

; ------------------------ Sections -------------------------------------------

Section "Main (required)" SEC_MAIN
  SectionIn RO

  ; Create directories
  SetOutPath "$INSTDIR"

  ; Copy application files
  ${IfNotThen} ${FileExists} "${SOURCE_DIR}\${MAIN_EXE}" ${|} MessageBox MB_ICONSTOP "MAIN_EXE not found in SOURCE_DIR: ${SOURCE_DIR}\${MAIN_EXE}$\r$\nProvide /DSOURCE_DIR and /DMAIN_EXE or stage files in .\payload" & Abort ${|}
  File /r "${SOURCE_DIR}\*.*"

  ; Create Start Menu & Desktop shortcuts
  SetShellVarContext all
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${MAIN_EXE}"
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${MAIN_EXE}"

  ; Write Uninstaller
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; Uninstall registry entries
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "${UNINST_KEY}" "Publisher" "${COMPANY_NAME}"
  WriteRegStr HKLM "${UNINST_KEY}" "URLInfoAbout" "${PUBLISHER_URL}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${MAIN_EXE}"
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair" 1
  ; Uninstall string
  WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
SectionEnd

Section "WebView2 Runtime (optional)" SEC_WEBVIEW2
!ifdef HAVE_WEBVIEW2
  ; If bootstrapper is provided, install WebView2 silently.
  ; Many systems already have it; running the bootstrapper twice is safe.
  DetailPrint "Installing Microsoft Edge WebView2 Runtime (if needed)…"
  ; Run the bootstrapper with silent flags
  nsExec::ExecToStack '"${WEBVIEW2_BOOTSTRAPPER}" /install /silent /norestart'
  Pop $0 ; return code text
  Pop $1 ; output (ignored)
  ; $0 will be "0" on success typically; do not abort if non-zero
!else
  DetailPrint "Skipping WebView2 install (no bootstrapper provided)."
!endif
SectionEnd

; ------------------------ Uninstaller ----------------------------------------

Section "Uninstall"
  SetShellVarContext all
  ; Kill running app? (best-effort) — rely on OS file-in-use prompts otherwise
  ; Delete shortcuts
  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  RMDir  "$SMPROGRAMS\${APP_NAME}"

  ; Remove files
  RMDir /r "$INSTDIR"

  ; Remove registry
  DeleteRegKey HKLM "${UNINST_KEY}"
SectionEnd

; ------------------------ Helper: Run app on Finish --------------------------
Function RunAnimicaExplorer
  Exec "$INSTDIR\${MAIN_EXE}"
FunctionEnd

; ------------------------ Version Resource (optional) ------------------------
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName"     "${APP_NAME}"
VIAddVersionKey "CompanyName"     "${COMPANY_NAME}"
VIAddVersionKey "FileDescription" "${APP_NAME} Installer"
VIAddVersionKey "FileVersion"     "${APP_VERSION}"
VIAddVersionKey "ProductVersion"  "${APP_VERSION}"
VIAddVersionKey "LegalCopyright"  "© ${COMPANY_NAME}"
