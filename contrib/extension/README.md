# Browser Extension (MV3) — Asset Pack

Static assets for the **Animica Wallet** browser extension (Manifest V3).  
These files are consumed by the extension’s `manifest.json` and store listings.

contrib/extension/
├─ README.md
├─ icons/
│ ├─ icon-16.png
│ ├─ icon-32.png
│ ├─ icon-48.png
│ └─ icon-128.png
├─ promo/
│ ├─ promo-440x280.png # small promo tile (Chrome Web Store)
│ └─ promo-1400x560.png # large marquee promo
└─ screens/
├─ overview.png
├─ send.png
└─ permissions.png

bash
Copy code

> Source artwork comes from `contrib/logos/` (SVG). If you need to regenerate icons, use the same `rsvg-convert` commands shown throughout `contrib/app-icons/*`.

---

## Using the icons in `manifest.json`

Minimal MV3 snippet:

```json
{
  "manifest_version": 3,
  "name": "Animica Wallet",
  "version": "0.1.0",
  "icons": {
    "16":  "contrib/extension/icons/icon-16.png",
    "32":  "contrib/extension/icons/icon-32.png",
    "48":  "contrib/extension/icons/icon-48.png",
    "128": "contrib/extension/icons/icon-128.png"
  },
  "action": {
    "default_icon": {
      "16":  "contrib/extension/icons/icon-16.png",
      "32":  "contrib/extension/icons/icon-32.png",
      "48":  "contrib/extension/icons/icon-48.png",
      "128": "contrib/extension/icons/icon-128.png"
    }
  },
  "background": { "service_worker": "background.js", "type": "module" },
  "permissions": [],
  "host_permissions": [],
  "content_scripts": [
    {
      "matches": ["<all_urls>"],
      "js": ["inject.js"],
      "run_at": "document_start"
    }
  ]
}
Notes

Keep permissions lean; request additional scopes at runtime via chrome.permissions.request.

The content script commonly injects a provider (e.g., window.animica) by creating a <script> tag that runs in the page context.

Store listing guidance
Chrome Web Store

Icons: up to 128×128 PNG (included above).

Screenshots: 1280×800 or larger (PNG/JPG). Use the screens/* as examples/placeholders.

Promo tile (small): promo-440x280.png

Marquee (large): promo-1400x560.png

Provide concise, action-oriented descriptions. Avoid dense text on images.

Firefox Add-ons (MV3)

Similar icon sizes apply. Verify your MV3 background SW uses supported APIs and avoid Chrome-only calls.

Regenerating icons from the SVG mark
From project root (macOS):

bash
Copy code
# Install rasterizer if needed
brew install librsvg

SRC=contrib/logos/animica-mark-only.svg
mkdir -p contrib/extension/icons

rsvg-convert -w 16  -h 16  "$SRC" -o contrib/extension/icons/icon-16.png
rsvg-convert -w 32  -h 32  "$SRC" -o contrib/extension/icons/icon-32.png
rsvg-convert -w 48  -h 48  "$SRC" -o contrib/extension/icons/icon-48.png
rsvg-convert -w 128 -h 128 "$SRC" -o contrib/extension/icons/icon-128.png
(Optionally) optimize PNGs:

bash
Copy code
brew install pngquant
pngquant --force --ext .png contrib/extension/icons/*.png
Local development (load unpacked)
Build/prepare your extension code (background/service worker, content scripts, UI).

In Chrome: chrome://extensions → Developer mode → Load unpacked → select your extension folder.

In Edge: edge://extensions → similar flow.

In Firefox Nightly: about:debugging#/runtime/this-firefox → Load Temporary Add-on.

Versioning
Any visual change here → add an entry under contrib/CHANGELOG.md (section Visuals → Extension).

Keep older assets on release branches for reproducible store submissions.

License
All extension assets © Animica. See contrib/LICENSING.md for third-party attributions and font/image licenses.
