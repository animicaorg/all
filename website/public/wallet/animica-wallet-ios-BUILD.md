# Animica Wallet (Flutter) — iOS Build Instructions

The IPA cannot be cross-compiled from Linux. Apple's tooling requires
macOS + Xcode and Apple does not redistribute the iOS SDK to non-Apple
hardware, so the iOS binary must be produced on a Mac.

## Prerequisites (macOS)
1. Xcode 15 or newer: install from the Mac App Store.
2. Flutter SDK 3.41+: https://docs.flutter.dev/get-started/install/macos
3. CocoaPods: `sudo gem install cocoapods` (or `brew install cocoapods`).
4. (For App Store distribution) an Apple Developer Program account and a
   provisioning profile bound to bundle id `org.animica.wallet`.

## Build (unsigned, for sideloading via Apple Configurator)
```bash
tar -xzf animica-wallet-flutter-ios-src.tar.gz
cd animica-wallet-flutter-ios-src/apps/wallet-mobile-flutter
flutter pub get
flutter build ios --release --no-codesign
# Output: build/ios/iphoneos/Runner.app
# To make a sideloadable .ipa:
mkdir -p Payload && cp -r build/ios/iphoneos/Runner.app Payload/
zip -r animica-wallet-ios.ipa Payload && rm -rf Payload
```

## Build (signed, for App Store / TestFlight)
```bash
flutter build ipa --release
# Output: build/ios/ipa/Runner.ipa  (signed with the dev cert keychain)
# Upload with: xcrun altool --upload-app -f build/ios/ipa/Runner.ipa ...
```

## Wallet features that need extra iOS configuration
- `flutter_inappwebview` requires the `NSAppTransportSecurity` plist
  entries if you intend to load HTTP (non-HTTPS) dapp origins (we don't
  — `walletProviderHosts` is HTTPS-only).
- `flutter_secure_storage` uses the Keychain by default — no extra
  permissions needed.
- `mobile_scanner` needs `NSCameraUsageDescription` in Info.plist (for
  QR scan on the Receive screen).
- `flutter_js` uses JavaScriptCore which is bundled with iOS — no extra
  setup. The ml_dsa_65.bundle.js asset signs ml_dsa_65 transactions on
  device.

## Why no prebuilt IPA in this release?
Animica's build infrastructure runs on Linux. We cross-compile Windows
and macOS desktop binaries from there, but iOS requires Apple-controlled
tooling we cannot replicate. Until we wire up a macOS CI runner, iOS
users must build from source as above, OR install the equivalent web
wallet (the chat-animica progressive web app at chat.animica.org which
can be added to the iOS home screen).
