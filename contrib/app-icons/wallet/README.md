# Wallet App Icons — Build & Integration

This folder contains platform app icons for the **Animica Wallet** (Flutter).  
Source of truth is an **SVG** logo (see `contrib/logos/animica-mark-only.svg` or full logo) rendered to platform PNGs/ICNS/ICO.

contrib/app-icons/wallet/
├─ README.md
├─ ios/AppIcon.appiconset/Contents.json
├─ ios/AppIcon.appiconset/Icon-App-1024x1024.png
├─ android/mipmap-xxxhdpi/ic_launcher.png
├─ android/mipmap-xxhdpi/ic_launcher.png
├─ android/mipmap-xhdpi/ic_launcher.png
├─ android/mipmap-hdpi/ic_launcher.png
├─ android/mipmap-mdpi/ic_launcher.png
├─ macos/icon.icns
├─ windows/icon.ico
└─ linux/hicolor/512x512/apps/animica-wallet.png

bash
Copy code

> Tip: keep the icon background **opaque** for store compliance. Prefer a safe 12–16 percent margin around the mark.

---

## Quick build from SVG

Pick your SVG source (mark only is recommended for small sizes):

- `contrib/logos/animica-mark-only.svg`
- or `contrib/logos/animica-logo.svg` (if you want the full lockup)

### Using `rsvg-convert` (librsvg)

```bash
# Install (macOS): brew install librsvg
SRC=contrib/logos/animica-mark-only.svg

# iOS 1024
rsvg-convert -w 1024 -h 1024 "$SRC" -o contrib/app-icons/wallet/ios/AppIcon.appiconset/Icon-App-1024x1024.png

# Android densities
rsvg-convert -w 48  -h 48  "$SRC" -o contrib/app-icons/wallet/android/mipmap-mdpi/ic_launcher.png
rsvg-convert -w 72  -h 72  "$SRC" -o contrib/app-icons/wallet/android/mipmap-hdpi/ic_launcher.png
rsvg-convert -w 96  -h 96  "$SRC" -o contrib/app-icons/wallet/android/mipmap-xhdpi/ic_launcher.png
rsvg-convert -w 144 -h 144 "$SRC" -o contrib/app-icons/wallet/android/mipmap-xxhdpi/ic_launcher.png
rsvg-convert -w 192 -h 192 "$SRC" -o contrib/app-icons/wallet/android/mipmap-xxxhdpi/ic_launcher.png

# Linux 512
rsvg-convert -w 512 -h 512 "$SRC" -o contrib/app-icons/wallet/linux/hicolor/512x512/apps/animica-wallet.png
macOS ICNS
bash
Copy code
# Requires: iconutil (Xcode CLTs)
TMP=contrib/app-icons/wallet/macos/Icon.iconset
mkdir -p "$TMP"
for s in 16 32 64 128 256 512; do
  rsvg-convert -w $s -h $s "$SRC" -o "$TMP/icon_${s}x${s}.png"
done
cp "$TMP/icon_32x32.png"   "$TMP/icon_16x16@2x.png"
cp "$TMP/icon_64x64.png"   "$TMP/icon_32x32@2x.png"
cp "$TMP/icon_256x256.png" "$TMP/icon_128x128@2x.png"
cp "$TMP/icon_512x512.png" "$TMP/icon_256x256@2x.png"
iconutil -c icns "$TMP" -o contrib/app-icons/wallet/macos/icon.icns
Windows ICO
bash
Copy code
# Requires: ImageMagick 'magick'
magick \
  \( "$SRC" -resize 16x16 \) \
  \( "$SRC" -resize 32x32 \) \
  \( "$SRC" -resize 48x48 \) \
  \( "$SRC" -resize 256x256 \) \
  contrib/app-icons/wallet/windows/icon.ico
If rsvg-convert is unavailable, use inkscape --export-filename=... or magick convert -background none -resize WxH.

Platform notes
iOS
Primary source: ios/AppIcon.appiconset/Icon-App-1024x1024.png

Apple’s asset compiler derives the required sizes from Contents.json (already included).

Ensure CFBundleIconName references AppIcon in ios/Runner/Info.plist.

Android
Density mapping used by Flutter:

mdpi 48, hdpi 72, xhdpi 96, xxhdpi 144, xxxhdpi 192.

Files live under android/app/src/main/res/mipmap-*/ic_launcher.png.

Network Security (dev only): if you allow cleartext, keep network_security_config.xml aligned with branding review requirements.

macOS
App icon bundle is macos/icon.icns.

Update macos/Runner/Assets.xcassets/AppIcon.appiconset/Contents.json if you switch to asset catalogs, or set bundle icon via project settings.

Windows
App icon is windows/runner/resources/app_icon.ico.

Replace or point CMake resource script to the generated icon.ico.

Linux
Provided hicolor 512 PNG under linux/hicolor/512x512/apps/animica-wallet.png.

Desktop files should reference animica-wallet.

Flutter integration
Option A: Manual (already wired in this repo)
The repo ships with platform-native icon placements. After replacing PNGs/ICNS/ICO, run:

bash
Copy code
flutter clean && flutter pub get
Option B: flutter_launcher_icons
If you prefer one-step generation, add this to your pubspec.yaml:

yaml
Copy code
dev_dependencies:
  flutter_launcher_icons: ^0.13.1

flutter_launcher_icons:
  image_path: contrib/app-icons/wallet/ios/AppIcon.appiconset/Icon-App-1024x1024.png
  android: true
  ios: true
  macos: true
  windows: true
  linux: true
Then:

bash
Copy code
flutter pub run flutter_launcher_icons
QA checklist
 Edges not clipped; safe margin preserved.

 No unintended transparency for store icons.

 Icons look crisp at 1x and 2x on dark/light backgrounds.

 App builds on all targets without asset warnings.

Versioning
Any visual change → bump entry under contrib/CHANGELOG.md (Visuals → App Icons).

Keep previous icons in release branches if store submissions depend on them.

