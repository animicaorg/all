# Animica — Brand Guide

Authoritative rules for using the Animica brand across products, web, press, and partner materials. For questions, open a PR or email brand@animica.dev.

---

## 1) Core Marks

**Primary logo (full lockup)**  
- File: `contrib/logos/animica-logo.svg` (light)  
- Dark variant: `contrib/logos/animica-logo-dark.svg`  
- Mark-only: `contrib/logos/animica-mark-only.svg`  
- Wordmarks: `contrib/logos/animica-wordmark*.svg`

**Do not** redraw, stretch, skew, recolor, add effects, place on noisy backgrounds without a keyline, or alter spacing.

---

## 2) Clear Space & Minimum Size

**Clear space**  
- Maintain free space around the logo equal to the **height of the “A”** in the wordmark (denote this as **A**).  
- No other graphics or text may enter this zone.

**Minimum sizes** (to preserve legibility)  
- Print: 25 mm width (lockup), 8 mm (mark-only).  
- Screen: 160 px width (lockup), 48 px (mark-only).  
- Favicons/app icons: use provided PNG/ICO/ICNS assets.

---

## 3) Color

Colors are sourced from design tokens. Prefer using tokens over hex values.

| Token                                   | Hex      | Usage                              |
|-----------------------------------------|----------|------------------------------------|
| `--anm-color-primary-600`               | #4B7DFF  | CTAs, highlights                   |
| `--anm-color-primary-700`               | #2E63FF  | Hover/active                       |
| `--anm-color-neutral-900`               | #0E1222  | Heading text (light theme)         |
| `--anm-color-neutral-50`                | #F6F8FF  | Surfaces (light theme)             |
| `--anm-color-surface-900` (dark)        | #0A0C14  | Surfaces (dark theme)              |
| `--anm-color-success-600`               | #22A06B  | Success states                     |
| `--anm-color-warning-600`               | #DFA71B  | Warning states                     |
| `--anm-color-error-600`                 | #E45757  | Error states                       |

**Backgrounds**  
- Light UI: logo (color or neutral-900) on neutral-50/white surfaces.  
- Dark UI: `animica-logo-dark.svg` on surface-900 or darker.  
- On imagery/noise, add a **1.5–2px** keyline (white at 30% alpha on dark; black at 25% alpha on light).

> Implement via tokens: include `contrib/tokens/build/css/tokens.css` and use CSS variables.

---

## 4) Typography

Primary typeface: **Inter** (OFL 1.1). Files are under `contrib/typography/web/inter/`.

- Headings: Inter SemiBold / Bold, tight tracking (-1 to -2), 1.15 line-height.  
- Body: Inter Regular, 0 to +0.2 tracking, 1.5 line-height.  
- Code/UI numbers: Inter Medium/Mono alternative (optional).

---

## 5) Layout & Spacing

Spacing tokens use a 4 px baseline:  
`space-1=4px, space-2=8px, 3=12px, 4=16px, 6=24px, 8=32px, 10=40px, 12=48px, 16=64px`.

Common patterns:  
- Card padding: `space-6` (24px)  
- Section vertical rhythm: `space-16` (64px)  
- Button horizontal padding: `space-4`–`space-5` (16–20px)  
- Border radii: `--anm-radius-lg` for cards, `--anm-radius-2xl` for hero modules.  
- Shadows: `--anm-shadow-md` (light), adjust to `--anm-shadow-sm` in dark mode.

---

## 6) Logo Usage Examples

**Correct**  
- On neutral-50 or surface-900 with correct variant.  
- Sufficient clear space (A).  
- Scaling preserves aspect ratio.  
- Keyline applied on complex backgrounds.

**Incorrect**  
- Recoloring outside brand palette.  
- Placing over low-contrast areas without keyline.  
- Adding drop shadows/glows or rotating/skewing.  
- Modifying letterforms or spacing.

(See `contrib/brand/BRAND_DONTs.md` for visual examples.)

---

## 7) Iconography

- System icons live in `contrib/icons/system/` (MIT). Keep 24×24 viewBox, 2px strokes, rounded joins by default.  
- Product icons live in `contrib/icons/product/`. Maintain consistent visual weight with system set.  
- Use the sprite at `contrib/icons/sprite/sprite.svg` for web; reference with `<use href="#icon-id">`.

---

## 8) Tone & Voice

Animica’s voice is **confident, precise, constructive**.

- **Clarity first**: prefer concrete numbers over vague claims.  
- **Engineering-grade**: explain tradeoffs succinctly.  
- **Inclusive & respectful**: no snark; credit open-source where used.  
- **Optimistic, not hyped**: avoid superlatives; show proof (benchmarks, audits).  
- **Actionable**: whenever possible, provide a next step (link, command, API call).

**Do**  
- “Deploy a local devnet with \`make devnet\`. Estimated time: ~90s.”  
- “This proposal reduces block gas by 15% (p95).”

**Don’t**  
- “World’s best chain.”  
- Vague timelines or unsubstantiated performance claims.

---

## 9) Accessibility

- Minimum contrast: **AA** for body text, **AAA** for small UI labels when feasible.  
- Do not signal state by color alone—pair with an icon or label.  
- Focus states must be visible and meet 3:1 contrast.  
- Motion: respect “prefers-reduced-motion”; provide non-animated fallbacks.

---

## 10) File Picking Matrix

| Context        | Asset                                  |
|----------------|----------------------------------------|
| Light UI       | `animica-logo.svg`                     |
| Dark UI        | `animica-logo-dark.svg`                |
| Tiny favicon   | `website/favicons/favicon-32x32.png`   |
| Social/OG      | `website/og/og-*.png`                  |
| App icon       | `app-icons/*` for each platform        |
| Extension      | `extension/icons/icon-*.png`           |
| Print          | SVG logos → export to PDF by printer   |

---

## 11) Legal

- Logos/wordmarks © Animica. See `contrib/LICENSING.md`.  
- Inter font is OFL 1.1.  
- Icons and tokens are MIT unless noted.  
- Do not imply partnership/sponsorship without written approval.

---

## 12) Updates & Versioning

- All visual changes must be reflected in `contrib/CHANGELOG.md`.  
- Token changes require bumping `tokens.json#version` and rebuilding artifacts.  
- For major brand changes, propose in a PR with mockups and rationale.

