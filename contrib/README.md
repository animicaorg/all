# Animica Contrib — Design & Brand Assets

This folder is the **single source of truth** for Animica brand, UI tokens, icons, logos, motion, and app-store/media kits. It’s structured to let multiple apps (Website, Explorer, Wallet, Browser Extension, Docs, Press) **consume the same assets** with predictable versioning.

---

## What’s here (high level)

- **brand/** — guidelines, grid, voice, accessibility.
- **tokens/** — canonical design tokens (color/typography/spacing/radii/shadows/animation) + builds for CSS/SCSS/TS/Dart/JSON, with schemas and tests.
- **typography/** — font binaries & usage snippets.
- **logos/** — wordmarks & marks (SVG + PNG renditions).
- **icons/** — system/product icons, SVGO config, sprite builder.
- **illustrations/** — hero meshes, section diagrams, patterns.
- **motion/** — Lottie/JSON, SVG anims, usage guide.
- **3d/** — GLB/USDZ + textures for hero renders.
- **app-icons/** — platform icon sets (Wallet/Explorer/Extension).
- **installers/** — DMG/AppImage/Windows splash/background images.
- **website/** — favicons, PWA maskables, OG images.
- **explorer/** — theme CSS + chart palettes/components.
- **email/** — MJML templates and build script.
- **docs-templates/** — press-release/whitepaper/decks.
- **press/** — boilerplate text, founder bios, photos, zipped logo packs.
- **social/** — banners/avatars + editable post templates.

> Large binaries (PNGs, GIFs, GLB/USDZ, PSD/Keynote/Docx) are tracked with **Git LFS**. See `contrib/.gitattributes`.

---

## Consuming assets across apps

### 1) Web (Website/Explorer/Docs)
- **Tokens (CSS):**
  ```html
  <link rel="stylesheet" href="/contrib/tokens/build/css/tokens.css">
  <link rel="stylesheet" href="/contrib/tokens/build/css/tokens.dark.css" media="(prefers-color-scheme: dark)">
Usage:

css
Copy code
.btn-primary { background: var(--anm-color-primary-600); border-radius: var(--anm-radius-lg); }
Icons (inline SVG or sprite):

html
Copy code
<!-- Sprite build output -->
<svg class="icon"><use href="/contrib/icons/sprite/sprite.svg#check"/></svg>
Or import a single SVG:

tsx
Copy code
import Check from "@/contrib/icons/system/check.svg";
Logos:

html
Copy code
<img src="/contrib/logos/animica-logo.svg" alt="Animica">
Explorer theme:

html
Copy code
<link rel="stylesheet" href="/contrib/explorer/themes/light.css">
<link rel="stylesheet" href="/contrib/explorer/themes/dark.css">
2) Wallet (Flutter)
Tokens (Dart bridge):

dart
Copy code
import 'package:animica_tokens/tokens.dart'; // if published
// or local:
// import 'contrib/tokens/build/dart/tokens.dart';
Fonts:
Add contrib/typography/flutter/fonts.yaml content into pubspec.yaml.

App icons:
Replace platform icon sets from contrib/app-icons/wallet/*.

3) Browser Extension (MV3)
Icons & promo images:
Copy from contrib/extension/icons/ and contrib/extension/promo/.

Screenshots:
See contrib/extension/screens/.

4) Email
Templates:
Build MJML to HTML:

bash
Copy code
node contrib/email/scripts/build.mjs
Outputs inline-styled HTML suitable for transactional emails.

5) Press & Social
Press kit:
Use contrib/press/kits/press-kit.zip (built artifact) for journalists.

Social:
Banners & avatars under contrib/social/*. Editable PSD/SVG templates included.

Versioning
We treat design assets like code:

SemVer for visual changes (recorded in contrib/CHANGELOG.md):

MAJOR: Breaking brand changes (logo redesign, token variable rename).

MINOR: New components/assets (additional icons, new OG image).

PATCH: Non-breaking tweaks (color fine-tuning, kerning, small spacing changes).

Token versions:

tokens.json contains "version": "x.y.z". Builds embed this version as a header comment.

Apps should pin to a tag or commit. For web, publish a cache-busted path, e.g. /contrib/tokens/build/css/tokens.css?v=1.2.3.

Asset pipeline outputs are deterministic; builds are committed to repo for reproducibility (CI can also regenerate and diff).

Build & Validate
Validate tokens schema & tests:

bash
Copy code
node contrib/tokens/scripts/validate.mjs
pnpm -C contrib/tokens test
Build tokens for all targets:

bash
Copy code
node contrib/tokens/scripts/build.mjs
Outputs:

CSS: contrib/tokens/build/css/{tokens.css,tokens.dark.css}

SCSS: contrib/tokens/build/scss/_tokens.scss

TS: contrib/tokens/build/ts/tokens.ts

Dart: contrib/tokens/build/dart/tokens.dart

JSON bundle: contrib/tokens/build/json/tokens.merged.json

Pack icon sprite:

bash
Copy code
node contrib/icons/scripts/make_sprite.mjs
Writes contrib/icons/sprite/sprite.svg.

Email build (MJML → HTML):

bash
Copy code
node contrib/email/scripts/build.mjs
Conventions
Color tokens use the scale: primary|neutral|success|warning|error × 50..900.

Spacing: 4px baseline (space-1 = 4px).

Radii: sm, md, lg, xl, 2xl.

Shadows: sm, md, lg tuned for light/dark.

Animation: durations/easings in tokens.animations.json.

SVGs must pass svgo (see contrib/icons/svgo.config.json).

Accessibility: contrast pairs documented in contrib/brand/ACCESSIBILITY.md.

Consuming via packages (optional)
You can publish a read-only package per platform:

NPM: @animica/design-tokens → ships /build/css, /build/ts, icons, logos.

Pub (Dart): animica_tokens → ships tokens.dart + example theme.

PyPI (if needed for docs tooling): animica-assets → serves OG images & tokens.

Pin exact versions in apps and update via Renovate/Dependabot.

LFS & repo hygiene
Binaries are declared in .gitattributes; ensure Git LFS is installed:

bash
Copy code
git lfs install
Avoid committing raw exports from design tools unless they’re canonical sources (e.g., Keynote decks in docs-templates/slide-deck/).

Run pre-commit hooks for SVGO and token validation.

Questions / Changes
Open a PR with:

A short description of the change and screenshots (light/dark).

Updated contrib/CHANGELOG.md.

For tokens: include a before/after snapshot of tokens.merged.json and note any breaking renames.

