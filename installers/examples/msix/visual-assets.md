# MSIX Visual Assets — sizes & naming matrix

This cheat-sheet lists the **required and recommended** image assets for a
desktop app packaged as **MSIX** (Windows 10/11). Use **PNG with alpha**,
no color profiles, and keep icons centered with padding (avoid edge-to-edge).

> **Scales:** Windows picks the best `scale-XXX` (100/125/150/200/400) for the
> current DPI. If you can’t produce all, ship **100/200** at minimum.
> Many shells also consume **targetsize-XX** variants for crisp small icons.

---

## 1) Core tile & icon assets (scale-based)

Place these under your `Assets/` (or equivalent) folder and reference them in
`Package.appxmanifest` (`Square44x44Logo`, `Square150x150Logo`, etc).

| Asset name            | Purpose / used in                         | Required | scale-100 (px) | scale-125 | scale-150 | scale-200 | scale-400 |
|-----------------------|-------------------------------------------|:--------:|:--------------:|:---------:|:---------:|:---------:|:---------:|
| **Square44x44Logo**   | Start menu small tile, taskbar jump list  |   ✔️    | 44×44          | 55×55     | 66×66     | 88×88     | 176×176   |
| **Square150x150Logo** | Start menu medium tile                    |   ✔️    | 150×150        | 188×188   | 225×225   | 300×300   | 600×600   |
| **Wide310x150Logo**   | Start menu wide tile                      |   ✔️    | 310×150        | 388×188   | 465×225   | 620×300   | 1240×600  |
| **Square310x310Logo** | Start menu large tile                     |   ⓘ     | 310×310        | 388×388   | 465×465   | 620×620   | 1240×1240 |
| **BadgeLogo**         | Notification/badge glyph (monochrome)     |   ⓘ     | 24×24          | 30×30     | 36×36     | 48×48     | 96×96     |
| **StoreLogo**         | Store/listing icon (legacy/optional)      |   ⓘ     | 50×50          | 63×63     | 75×75     | 100×100   | 200×200   |

Notes:

- **Required**: Microsoft generally expects the first three for a healthy package.
- **BadgeLogo** should be **single-color** (white) on transparent canvas.
- Windows 11 tiles are less prominent, but these assets are still consumed by shell surfaces.

**Naming convention (examples):**

Assets/Square44x44Logo.scale-100.png
Assets/Square44x44Logo.scale-200.png
Assets/Square150x150Logo.scale-200.png
Assets/Wide310x150Logo.scale-200.png
Assets/Square310x310Logo.scale-200.png
Assets/BadgeLogo.scale-200.png

---

## 2) Small crisp icons (targetsize-based, recommended)

For taskbar, notification area, file associations, and jump lists Windows prefers
**pixel-perfect** sizes instead of scaled-down big icons. Ship **targetsize** variants:

| Base name                 | Variants (px)                      | Example filenames                                                 |
|---------------------------|------------------------------------|-------------------------------------------------------------------|
| **Square44x44Logo**       | 16, 24, 32, 48 (and optionally 20) | `Square44x44Logo.targetsize-16.png`, `...targetsize-24.png`, etc. |
| **Square44x44Logo (unplated)** | 16, 24, 32, 48                 | `Square44x44Logo.targetsize-16_altform-unplated.png`              |

> **Unplated** avoids OS accent “plate”. Use it for detailed icons on Windows 11.

You can provide both `targetsize-XX.png` **and** matching `_altform-unplated.png`.
Windows will pick the best fit per surface and theme.

---

## 3) Manifest wiring (snippet)

Make sure your `Package.appxmanifest` references the correct base names
(**without** the DPI suffix). Windows will probe matching `scale-XXX` / `targetsize-XX`
files automatically.

```xml
<uap:VisualElements
  DisplayName="Animica Wallet"
  Square44x44Logo="Assets/Square44x44Logo.png"
  Square150x150Logo="Assets/Square150x150Logo.png"
  Description="Animica Wallet"
  BackgroundColor="transparent">
  <uap:DefaultTile
    Wide310x150Logo="Assets/Wide310x150Logo.png"
    Square310x310Logo="Assets/Square310x310Logo.png" />
  <uap:SplashScreen Image="Assets/SplashScreen.png" BackgroundColor="transparent"/>
</uap:VisualElements>

If you ship unplated small icons, add:

<uap5:AppIcon>
  <uap5:Icon Image="Assets/Square44x44Logo.targetsize-16.png" />
  <uap5:Icon Image="Assets/Square44x44Logo.targetsize-20.png" />
  <uap5:Icon Image="Assets/Square44x44Logo.targetsize-24.png" />
  <uap5:Icon Image="Assets/Square44x44Logo.targetsize-32.png" />
  <uap5:Icon Image="Assets/Square44x44Logo.targetsize-48.png" />
  <uap5:Icon Image="Assets/Square44x44Logo.targetsize-16_altform-unplated.png" />
  <uap5:Icon Image="Assets/Square44x44Logo.targetsize-24_altform-unplated.png" />
</uap5:AppIcon>

(Ensure the uap5 namespace is declared in the <Package> root for your SDK target.)

⸻

4) Quick asset checklist
	•	Transparent PNG, square canvas where applicable, 32-bit RGBA.
	•	Provide at least scale-100 and scale-200 for all core assets.
	•	Provide targetsize 16/24/32/48 for small/icon surfaces.
	•	Consider _altform-unplated variants for Windows 11.
	•	No text baked into icons; ensure legibility at 16–24 px.
	•	Avoid pure black on transparent (can disappear in dark mode); use subtle stroke.
	•	Keep consistent padding (safe area) across sizes.

⸻

5) Example file tree

Assets/
├── Square44x44Logo.png                         # logical base (not used directly)
├── Square44x44Logo.scale-100.png
├── Square44x44Logo.scale-200.png
├── Square44x44Logo.targetsize-16.png
├── Square44x44Logo.targetsize-24.png
├── Square44x44Logo.targetsize-32.png
├── Square44x44Logo.targetsize-48.png
├── Square44x44Logo.targetsize-16_altform-unplated.png
├── Square150x150Logo.scale-100.png
├── Square150x150Logo.scale-200.png
├── Wide310x150Logo.scale-100.png
├── Wide310x150Logo.scale-200.png
├── Square310x310Logo.scale-200.png
├── BadgeLogo.scale-200.png
└── SplashScreen.png


⸻

6) Sizing quick reference

If your designer works from vector source, export the following scale-200
targets (Windows will downsample gracefully if others are missing):
	•	Square44x44Logo.scale-200 → 88×88
	•	Square150x150Logo.scale-200 → 300×300
	•	Wide310x150Logo.scale-200 → 620×300
	•	Square310x310Logo.scale-200 → 620×620
	•	BadgeLogo.scale-200 → 48×48
	•	Target sizes: 16×16, 24×24, 32×32, 48×48 (+ unplated)

⸻

7) Validation tips
	•	Use AppxManifest Schema validation (Visual Studio or Windows App SDK tooling).
	•	Install locally and check Start menu, taskbar, notification area, and file associations.
	•	For WinGet: ensure your MSIX PackageFamilyName is consistent with your icons.

