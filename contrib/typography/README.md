# Animica Typography

This package provides a consistent type system for **Website, Explorer, Wallet Extension, Flutter Wallet, Studio**, and docs.

- Canonical font: **Inter** (Variable + Italic). Files live in `typography/web/inter/`.
- Token-driven: sizes/line-heights/weights come from `contrib/tokens/tokens.json`.
- Ready-to-use CSS helpers in `typography/css/typography.css`.
- Flutter setup snippet in `typography/flutter/fonts.yaml`.

> See `contrib/typography/licenses/INTER_LICENSE.txt` for licensing.

---

## Contents

typography/
├─ README.md ← this file
├─ scale.json ← modular scale + line-height map (source of truth)
├─ web/
│ ├─ inter/Inter-Variable.woff2
│ └─ inter/Inter-Italic.woff2
├─ css/typography.css ← utility classes & mixins
├─ flutter/fonts.yaml ← pubspec snippet
└─ licenses/INTER_LICENSE.txt

css
Copy code

---

## Design Principles

1. **Token-first**: Do not hard-code sizes in apps. Consume tokens or CSS utilities.
2. **Readable by default**: comfortable defaults (`line-height: 1.5`, modest tracking).
3. **Scale, not steps**: use the named scale (`xs, sm, base, md, lg, xl, 2xl, 3xl, 4xl`).
4. **Accessible contrast**: pair with color tokens that meet WCAG AA/AAA (see `contrib/brand/ACCESSIBILITY.md`).

---

## Web / React (CSS)

### Install / Import

```css
/* Global app CSS */
@font-face {
  font-family: "Inter";
  src: url("/contrib/typography/web/inter/Inter-Variable.woff2") format("woff2-variations");
  font-weight: 100 900;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: "Inter";
  src: url("/contrib/typography/web/inter/Inter-Italic.woff2") format("woff2");
  font-style: italic;
  font-display: swap;
}

@import url("/contrib/tokens/build/css/tokens.css");       /* light */
@import url("/contrib/tokens/build/css/tokens.dark.css");  /* dark overrides */
@import url("/contrib/typography/css/typography.css");     /* utilities */
Utilities (from css/typography.css)
Family: .font-base (Inter), .font-code (monospace)

Sizes: .text-xs | .text-sm | .text-base | .text-md | .text-lg | .text-xl | .text-2xl | .text-3xl | .text-4xl

Line height: .leading-tight | .leading-normal | .leading-loose

Weight: .weight-regular | .weight-medium | .weight-semibold | .weight-bold

Tracking: .track-tight | .track-normal | .track-loose

Example:

html
Copy code
<h1 class="font-base text-3xl leading-tight weight-semibold">Animica</h1>
<p class="font-base text-base leading-normal track-normal">Build useful work.</p>
<code class="font-code text-sm">animica_callContract(...)</code>
All utilities are generated from CSS variables set by tokens:
--anm-typography-size-*, --anm-typography-line-*, --anm-typography-weight-*

Flutter
Add the fonts to your pubspec.yaml (use typography/flutter/fonts.yaml as a starting point), then wire a theme:

dart
Copy code
import 'package:flutter/material.dart';

final textTheme = TextTheme(
  displayLarge:  TextStyle(fontFamily: 'Inter', fontSize: 40, height: 1.15, fontWeight: FontWeight.w700),
  headlineLarge: TextStyle(fontFamily: 'Inter', fontSize: 32, height: 1.15, fontWeight: FontWeight.w600),
  headlineMedium:TextStyle(fontFamily: 'Inter', fontSize: 24, height: 1.15, fontWeight: FontWeight.w600),
  titleLarge:    TextStyle(fontFamily: 'Inter', fontSize: 20, height: 1.5,  fontWeight: FontWeight.w600),
  bodyLarge:     TextStyle(fontFamily: 'Inter', fontSize: 16, height: 1.5,  fontWeight: FontWeight.w400),
  bodyMedium:    TextStyle(fontFamily: 'Inter', fontSize: 14, height: 1.5,  fontWeight: FontWeight.w400),
  labelLarge:    TextStyle(fontFamily: 'Inter', fontSize: 14, height: 1.5,  fontWeight: FontWeight.w500),
);

// Combine with color tokens → ThemeData in contrib/tokens/build/dart/tokens.dart
scale.json
scale.json provides a modular scale and line-height map (source for CSS generation):

json
Copy code
{
  "fontFamily": { "base": "Inter", "code": "ui-monospace" },
  "scale": { "xs": 12, "sm": 14, "base": 16, "md": 18, "lg": 20, "xl": 24, "2xl": 32, "3xl": 40, "4xl": 48 },
  "lineHeight": { "tight": 1.15, "normal": 1.5, "loose": 1.7 },
  "weight": { "regular": 400, "medium": 500, "semibold": 600, "bold": 700 },
  "tracking": { "tight": -0.01, "normal": 0, "loose": 0.01 }
}
The CSS builder (contrib/tokens/scripts/build.mjs) reads token values; typography utilities reference the same variables.

Dark Mode
Typography itself is color-agnostic. When switching themes:

js
Copy code
document.documentElement.setAttribute('data-theme', 'dark'); // uses tokens.dark.css overrides
Ensure text colors use --anm-color-neutral-900 (light) and corresponding dark overrides for readability.

Accessibility
Minimum sizes: body text ≥ 14px (.text-sm) recommended; default 16px is better.

Headings: preserve hierarchy; don’t skip levels.

Contrast: pair text with neutral/surface tokens meeting WCAG AA (see brand accessibility doc).

Motion: respect prefers-reduced-motion; our animations can be disabled via tokens.

Versioning
Typography follows the tokens version (semver). Breaking changes in typography.css utilities bump the minor at least.

Local Preview
bash
Copy code
# Rebuild token CSS (if you changed tokens)
node contrib/tokens/scripts/build.mjs

# Open a quick demo (requires any static server)
npx http-server . -o /contrib/typography/demo.html
(Optional: add your own demo.html that exercises the utilities.)

Gotchas
Always load token CSS before typography utilities.

If your bundler scopes assets, ensure font URLs resolve to Inter-Variable.woff2 and Inter-Italic.woff2.

For server-rendered apps, use font-display: swap to avoid FOIT.

