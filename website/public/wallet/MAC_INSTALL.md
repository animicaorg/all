# Installing the Mac Qt wallet

The Mac build is **cross-compiled from Linux and ad-hoc signed** — it
runs fine on macOS, but Gatekeeper will warn on first launch because
the bundle isn't notarized by Apple. Two-step install:

## 1. Download + unzip
```
curl -L -o animicawalletmac.zip https://animica.org/wallet/animicawalletmac.zip
unzip animicawalletmac.zip
```

You should now have `AnimicaWallet.app` in your current directory.

## 2. Clear the quarantine attribute and launch

macOS sets a `com.apple.quarantine` extended attribute on anything
downloaded with a browser. Even ad-hoc-signed apps refuse to launch
when that attribute is present. Clear it once:

```
xattr -dr com.apple.quarantine AnimicaWallet.app
open AnimicaWallet.app
```

That's it. Subsequent launches work as normal — double-click from
Finder, drag to /Applications, etc.

## If you get "AnimicaWallet is damaged and can't be opened"

That message means quarantine is still set. Run the `xattr -dr`
command above. If you got the zip via something other than `curl`
(Safari, Chrome, AirDrop) the quarantine attribute is always added.

## If you get "AnimicaWallet can't be opened because Apple cannot
check it for malicious software"

That's the standard ad-hoc-signed app warning. Either:

1. Right-click the .app in Finder → "Open" → "Open" in the confirmation
   dialog. macOS remembers this one-time approval.
2. Or: System Settings → Privacy & Security → scroll to "AnimicaWallet
   was blocked..." → click "Open Anyway".

## What's running inside

The app is unmodified Qt 6.7 + the Animica wallet code from
`apps/wallet-qt/` in the source tree. It connects to
`https://rpc.animica.org/rpc` by default for chain queries. Settings
let you point it at any other Animica RPC endpoint.

## Why isn't it notarized?

Notarization requires uploading the binary to Apple, which requires a
$99/year Apple Developer ID account and a Mac to run `notarytool` on
(or a build server with Apple-tooling access). Until the project has
either, all Mac binaries ship ad-hoc-signed.
