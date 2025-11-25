; Animica Wallet — Optional NSIS Installer
; Build (from repo root):
;   makensis ^
;     /DAPP_NAME="Animica Wallet" ^
;     /DVERSION=1.2.3 ^
;     /DARCH=x64 ^
;     /DOUTPUT="dist\windows\stable\Animica-Wallet-Setup-1.2.3.exe" ^
;     /DSOURCE_DIR="build\windows\x64\runner\Release" ^
;     installers\wallet\windows\nsis\installer.nsi
;
; Notes:
;   • Prefer passing /DSOURCE_DIR to include the whole app directory (EXE + DLLs + assets).
;   • If you only pass /DBINARY, only that single file will be installed (likely insufficient).
;   • Sign the produced installer with installers/wallet/windows/codesign.ps1.

Unicode True
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"

; ----------- Parameters / Defaults -----------
!ifndef APP_NAME
  !define APP_NAME "Animica Wallet"
!endif

!ifndef VERSION
  !define VERSION "1.0.0"
!endif

!ifndef ARCH
  !define ARCH "x64"
!endif

!ifndef OUTPUT
  !define OUTPUT "dist\windows\${APP_NAME}-Setup-${VERSION}.exe"
!endif

; Optional: full directory to package (recommended)
; !define SOURCE_DIR "build\windows\x64\runner\Release"

; Optional: single binary to package (fallback)
; !define BINARY "build\windows\x64\runner\Release\Animica Wallet.exe"

; Optional AppID for ARP
!ifndef APP_ID
  !define APP_ID "AnimicaWallet"
!endif

; Optional Publisher / URL
!ifndef PUBLISHER
  !define PUBLISHER "Animica Labs, Inc."
!endif
!ifndef HOMEPAGE_URL
  !define HOMEPAGE_URL "https://animica.dev"
!endif

OutFile "${OUTPUT}"
Name "${APP_NAME}"
Caption "${APP_NAME} Setup"
BrandingText "Animica — ${APP_NAME}"

; Version info
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName"     "${APP_NAME}"
VIAddVersionKey "ProductVersion"  "${VERSION}"
VIAddVersionKey "CompanyName"     "${PUBLISHER}"
VIAddVersionKey "FileDescription" "${APP_NAME} Installer"
VIAddVersionKey "FileVersion"     "${VERSION}"

; Install location (64-bit Program Files by default)
!define INSTALL_SUBDIR "${APP_NAME}"
InstallDir "$PROGRAMFILES64\${INSTALL_SUBDIR}"

; MUI: Pages (License is optional if file exists)
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME

!ifexist "..\..\EULA.txt"
  LicenseData "..\..\EULA.txt"
  !insertmacro MUI_PAGE_LICENSE "..\..\EULA.txt"
!endif

!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES

; Finish page with "Run" checkbox
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApp
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

; ----------- Sections -----------

Section "Install"
  SetOutPath "$InstDir"

  ; Copy payload
  !ifdef SOURCE_DIR
    DetailPrint "Installing from directory: ${SOURCE_DIR}"
    ; Exclude PDBs and build junk
    File /r /x *.pdb /x *.ilk /x *.exp /x *.lib "${SOURCE_DIR}\*.*"
  !else
    !ifdef BINARY
      DetailPrint "Installing single binary: ${BINARY}"
      File "${BINARY}"
    !else
      MessageBox MB_ICONSTOP "No payload specified. Define /DSOURCE_DIR=<dir> (recommended) or /DBINARY=<exe>."
      Abort
    !endif
  !endif

  ; Create Start Menu and Desktop shortcuts
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$InstDir\${APP_NAME}.exe"
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$InstDir\${APP_NAME}.exe"

  ; Write Uninstaller
  WriteUninstaller "$InstDir\Uninstall.exe"

  ; Add/Remove Programs entry
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "DisplayName"     "${APP_NAME}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "DisplayVersion"  "${VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "Publisher"       "${PUBLISHER}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "URLInfoAbout"    "${HOMEPAGE_URL}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "InstallLocation" "$InstDir"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "DisplayIcon"     "$InstDir\${APP_NAME}.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "UninstallString" '"$InstDir\Uninstall.exe"'
  ; Estimated size (KB): compute roughly by directory size at install time
  ${GetSize} "$InstDir" "/S=0K" $0 $1 $2
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "EstimatedSize" "$0"

SectionEnd

Section "Uninstall"
  ; Remove shortcuts
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  RMDir  "$SMPROGRAMS\${APP_NAME}"
  Delete "$DESKTOP\${APP_NAME}.lnk"

  ; Remove files
  RMDir /r "$InstDir"

  ; Remove ARP keys
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}"
SectionEnd

; ----------- Functions -----------

Function .onInit
  ; Prefer 64-bit Program Files on 64-bit OS
  ${If} ${RunningX64}
    StrCpy $InstDir "$PROGRAMFILES64\${INSTALL_SUBDIR}"
  ${Else}
    StrCpy $InstDir "$PROGRAMFILES\${INSTALL_SUBDIR}"
  ${EndIf}
FunctionEnd

Function LaunchApp
  ; Only run if the EXE exists
  IfFileExists "$InstDir\${APP_NAME}.exe" 0 +2
    Exec "$InstDir\${APP_NAME}.exe"
FunctionEnd

; ----------- Build-time Summary -----------
!echo "NSIS building ${APP_NAME} v${VERSION} (${ARCH}) → ${OUTPUT}"
!ifdef SOURCE_DIR
  !echo " • SOURCE_DIR = ${SOURCE_DIR}"
!endif
!ifdef BINARY
  !echo " • BINARY     = ${BINARY}"
!endif
