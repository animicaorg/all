# Animica Contrib — Changelog

All notable visual & token changes will be documented in this file.  
This project adheres to **Semantic Versioning** and the **Keep a Changelog** format.

> **Scope**: Only design-system and asset changes in `contrib/` (tokens, icons, logos, motion, illustrations, app icons, email templates, explorer themes, etc.).

---

## [Unreleased]

### Added
- _Placeholder_: Add new system icons (`info`, `warning`, `error`) to `contrib/icons/system/`.
- _Placeholder_: New OG image `contrib/website/og/og-studio.png`.

### Changed
- _Placeholder_: Tune primary color scale contrast (700/800) for WCAG AA.
- _Placeholder_: Typography tracking -1 on headings h1–h3.

### Fixed
- _Placeholder_: SVG logo precision issues at 1x scale.
- _Placeholder_: Dark theme token mismatch for `--anm-color-surface-50`.

### Removed
- _Placeholder_: Deprecated badge icon `badge-old.svg`.

### Tokens
- **Version**: `tokens.json#version` → _bump here when releasing_
- **Breaking**: _list any renamed/removed tokens_
- **Notes**: Run `node contrib/tokens/scripts/build.mjs` after any token change.

---

## [0.1.0] - 2025-10-31

### Added
- Initial release of **Animica Contrib**.
- Canonical **design tokens**: `contrib/tokens/tokens.json`, `tokens.dark.json`, `tokens.animations.json`.
- **Builds** for CSS/SCSS/TS/Dart/JSON in `contrib/tokens/build/*`.
- **Logos & wordmarks**: light/dark SVGs + PNG renditions.
- **System & product icons** with SVGO config and sprite builder.
- **Explorer themes** (light/dark) and chart palette.
- **Email templates** (MJML) and build script.
- **Press kit** skeleton and social banners.
- **3D orb** placeholders and textures.
- **Typography** (Inter) with licenses and CSS helpers.

### Changed
- N/A (first cut)

### Fixed
- N/A

### Tokens
- **Version**: `1.0.0`
- **Breaking**: None.
- **Notes**: Token scale uses 50–900 steps; spacing baseline 4px.

---

## Release Process

1. Update **[Unreleased]** with changes.
2. Bump `contrib/tokens/tokens.json > version` if tokens changed.
3. Rebuild artifacts:
   ```bash
   node contrib/tokens/scripts/validate.mjs
   node contrib/tokens/scripts/build.mjs
   node contrib/icons/scripts/make_sprite.mjs
   node contrib/email/scripts/build.mjs
Move entries from [Unreleased] to a new tag section:

css
Copy code
## [x.y.z] - YYYY-MM-DD
Commit:

scss
Copy code
chore(contrib): release x.y.z
Tag:

css
Copy code
git tag contrib-vx.y.z && git push --tags
Conventional Commit Hints
feat(tokens): add success-950 color

fix(icons): correct viewBox for warning.svg

chore(email): inline CSS for Outlook

refactor(tokens): rename primary-500 → brand-500 (BREAKING)

docs(brand): clarify clear-space rules

Links
Compare changes: link your repo diff here once public

Design source of truth: contrib/tokens/tokens.json

Accessibility guidelines: contrib/brand/ACCESSIBILITY.md

