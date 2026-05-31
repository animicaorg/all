# Installing the Windows Qt wallet

The Windows build is **cross-compiled from Linux with mingw-w64 and
unsigned** — it runs fine on Windows 10/11, but SmartScreen and
Defender will warn on first launch because the binary doesn't have a
trusted Authenticode signature. The warning is *expected*; you click
through it once.

## Two flavors available

| Filename | Use when |
|---|---|
| [`animicawallersetup.exe`](https://animica.org/wallet/animicawallersetup.exe) (19 MB) | NSIS installer — adds Start Menu shortcut + uninstaller |
| [`animica-wallet-windows-x64.zip`](https://animica.org/wallet/animica-wallet-windows-x64.zip) (26 MB) | Portable — extract anywhere, run `animica-wallet.exe` |

## First-launch SmartScreen warning

When you run either binary, Windows will pop up:

> Windows protected your PC
>
> Microsoft Defender SmartScreen prevented an unrecognized app from starting.

Click **More info** → **Run anyway**. After approving once, future
launches don't show the warning.

## If Defender quarantines the .exe

Unsigned mingw-built executables sometimes trigger heuristic AV
flags (especially on freshly compiled binaries with low reputation).
If Defender quarantines it:

1. Open Windows Security → Virus & threat protection → Protection history
2. Find the quarantined "animica-wallet.exe" entry
3. Click Actions → Allow on device
4. Re-download from animica.org/wallet/ and verify the SHA-256

Both .exe and .zip ship the SHA-256 checksum at
`https://animica.org/wallet/animicawallersetup.sha256` and
`.../animica-wallet-windows.sha256` respectively.

## What's inside

- `animica-wallet.exe` — the Qt 6.7 GUI
- `Qt6Core.dll`, `Qt6Gui.dll`, `Qt6Widgets.dll`, `Qt6Network.dll`,
  `Qt6Sql.dll`, `Qt6Svg.dll` — bundled Qt
- `libgcc_s_seh-1.dll`, `libstdc++-6.dll`, `libwinpthread-1.dll` —
  mingw runtime
- `libcrypto-3-x64.dll` — OpenSSL for TLS
- `platforms/`, `sqldrivers/`, `tls/`, `imageformats/`,
  `networkinformation/`, `styles/` — Qt plugins

No installer registry pollution beyond the uninstall key. No
admin/UAC required for the installer (per-user install scope).

## Why isn't it signed?

Authenticode code-signing requires a $100-300/year certificate from
a trusted CA (DigiCert, Sectigo, etc.) and a Mac/Windows machine to
run `signtool` on. Until the project has a CA-issued cert,
all Windows binaries ship unsigned and require the SmartScreen
"Run anyway" click-through.

## Source

Built from `wallet-qt/` in the source tree via
`scripts/release-windows-cross.sh`. mingw-w64 toolchain + Qt 6.7.0
mingw + OpenSSL 3.6.2.
