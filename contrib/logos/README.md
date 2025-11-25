# Animica Logos & Wordmarks

This folder contains the **official Animica marks** for use across Website, Explorer, Wallet Extension, Flutter Wallet, Studio, CEX/DEX UIs, social, and press.

> For color/spacing/voice rules, see `contrib/brand/BRAND_GUIDE.md` and `contrib/brand/BRAND_DONTs.md`. Accessibility guidance lives in `contrib/brand/ACCESSIBILITY.md`.

---

## What’s here

contrib/logos/
├─ README.md ← this file
├─ animica-logo.svg ← primary lockup (horizontal)
├─ animica-logo-dark.svg ← for dark surfaces (pre-tinted)
├─ animica-wordmark.svg ← wordmark only
├─ animica-wordmark-dark.svg
├─ animica-mark-only.svg ← symbol-only (square-friendly)
├─ png/
│ ├─ animica-logo-512.png
│ └─ animica-logo-1024.png
├─ monochrome/
│ ├─ animica-logo-white.svg
│ └─ animica-logo-black.svg
└─ safe-area-mask.svg ← clearspace visualization mask

yaml
Copy code

**When to use which:**
- `animica-logo.svg`: default for light backgrounds.
- `animica-logo-dark.svg`: default for dark backgrounds.
- `animica-mark-only.svg`: avatars, favicons, badges, app icons.
- `monochrome/*`: embossing, one-color prints, laser/engrave.
- `png/*`: when raster is required (social, presentations).

---

## Color

Primary brand colors are sourced from tokens:
- Light UI: `--anm-color-primary-600`, surface text on neutral backgrounds.
- Dark UI: use `animica-logo-dark.svg` or set logo fill to `--anm-color-primary-300` with adequate contrast.

**Minimum contrast:** logo vs. background should meet **WCAG AA** for non-text symbols where possible (see `ACCESSIBILITY.md`).

---

## Clear Space & Minimum Size

- Use the `safe-area-mask.svg` to visualize **clear space**. Keep surrounding elements outside the mask boundary.
- **Minimum display size**:
  - Horizontal logo: 120px width on web, 24pt in print.
  - Mark-only: 32px square on web, 8mm in print.

---

## Don’ts (quick list)

- ❌ Recolor arbitrarily (stick to tokens or monochrome set).
- ❌ Stretch, skew, or add effects (glow, drop shadow) to the vectors.
- ❌ Alter spacing between symbol and wordmark.
- ❌ Place on busy imagery without a contrast-safe backdrop.

See full list in `contrib/brand/BRAND_DONTs.md`.

---

## Implementation Snippets

### HTML (inline SVG recommended)

```html
<link rel="preload" href="/contrib/tokens/build/css/tokens.css" as="style" onload="this.rel='stylesheet'">
<img src="/contrib/logos/animica-logo.svg" alt="Animica" width="240" height="auto">
React
tsx
Copy code
import Logo from '/contrib/logos/animica-logo.svg?url';
export function Brand() {
  return <img src={Logo} alt="Animica" style={{ height: 28 }} />;
}
CSS mask (for duotone/emboss)
css
Copy code
.brand-mask {
  -webkit-mask: url('/contrib/logos/animica-mark-only.svg') no-repeat center / contain;
  mask: url('/contrib/logos/animica-mark-only.svg') no-repeat center / contain;
  background: var(--anm-color-primary-600);
  width: 32px; height: 32px;
}
Export Guidance
Master sources: SVGs are the single source of truth. Generate PNGs with rsvg-convert or your vector editor.

Raster export:

512px and 1024px PNGs included (RGB, transparent).

For iOS/Android/desktop icons, see contrib/app-icons/*.

Example CLI (mac/Linux):

bash
Copy code
# 512px PNG from SVG
rsvg-convert -w 512 -h 512 contrib/logos/animica-mark-only.svg \
  -o contrib/logos/png/animica-logo-512.png
Versioning & Changes
Any visual change to logos should bump contrib/CHANGELOG.md (Visuals section).

Keep exports deterministic: same paths, same filenames.

If changing glyph geometry, regenerate downstream app-icons and press-kit.

Licensing
Logos & wordmarks © Animica. Redistribution permitted within Animica projects and press coverage.

Third-party use requires written permission unless covered by a published brand policy.

Contact
For brand approvals or new format requests, open an issue in the Brand workspace or contact design@animica.dev.

