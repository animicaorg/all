# Animica Design Tokens

Canonical, cross-platform **design tokens** for color, typography, spacing, radii, shadows, and motion.  
These JSON sources generate platform builds for **Web (CSS/SCSS/TS)**, **Flutter (Dart)**, and **JSON bundles**.  
Tokens are treated like code: versioned, validated, and built deterministically.

---

## Contents

- `tokens.json` — Light theme canonical tokens (colors, type, spacing, radii, shadows)
- `tokens.dark.json` — Dark theme overrides (only keys that differ from light)
- `tokens.animations.json` — Durations, easings, motion presets
- `schemas/tokens.schema.json` — JSON Schema for validation
- `build/` — Generated artifacts (CSS, SCSS, TS, Dart, JSON)
- `scripts/` — Build & validate utilities
- `tests/` — Unit tests for invariants and schema validation

> Large binaries are not stored here. SVGs and code are plain text; see `contrib/.gitattributes`.

---

## Token Model (High-Level)

```jsonc
{
  "version": "1.0.0",
  "color": {
    "primary": { "50": "#EEF3FF", "100": "#DCE7FF", "...": "#2E63FF", "900": "#0D1B3D" },
    "neutral": { "50": "#F6F8FF", "100": "#ECEFFC", "...": "#0E1222" },
    "success": { "50": "...", "600": "#22A06B", "...": "..." },
    "warning": { "600": "#DFA71B" },
    "error":   { "600": "#E45757" }
  },
  "typography": {
    "fontFamily": { "base": "Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif" },
    "scale": { "xs": 12, "sm": 14, "base": 16, "md": 18, "lg": 20, "xl": 24, "2xl": 32, "3xl": 40 },
    "lineHeight": { "tight": 1.15, "normal": 1.5, "loose": 1.7 },
    "tracking": { "tight": -0.01, "normal": 0, "loose": 0.01 }
  },
  "space": { "1": 4, "2": 8, "3": 12, "4": 16, "6": 24, "8": 32, "10": 40, "12": 48, "16": 64 },
  "radius": { "sm": 6, "md": 10, "lg": 14, "xl": 18, "2xl": 24 },
  "shadow": {
    "sm": "0 1px 2px rgba(0,0,0,.06)",
    "md": "0 2px 8px rgba(0,0,0,.08)",
    "lg": "0 8px 24px rgba(0,0,0,.10)"
  }
}
Dark tokens only override changed values (e.g., surfaces, neutrals, elevations).

Using the Builds
Web (CSS variables)
Include in your HTML:

html
Copy code
<link rel="stylesheet" href="/contrib/tokens/build/css/tokens.css">
<link rel="stylesheet" href="/contrib/tokens/build/css/tokens.dark.css" media="(prefers-color-scheme: dark)">
Use in CSS:

css
Copy code
.card {
  background: var(--anm-color-neutral-50);
  color: var(--anm-color-neutral-900);
  border-radius: var(--anm-radius-lg);
  box-shadow: var(--anm-shadow-md);
  padding: var(--anm-space-6);
}
SCSS
scss
Copy code
@use "contrib/tokens/build/scss/tokens" as anm;

.button {
  background: anm.$color-primary-600;
  border-radius: anm.$radius-lg;
}
TypeScript
ts
Copy code
import tokens from "@/contrib/tokens/build/ts/tokens";
console.log(tokens.color.primary["600"]);
Flutter (Dart)
dart
Copy code
import 'contrib/tokens/build/dart/tokens.dart' as anm;

final primary600 = anm.color.primary.$600; // example accessor
JSON Bundle
js
Copy code
import merged from "@/contrib/tokens/build/json/tokens.merged.json";
Build & Validate
Validate schemas and invariants:

bash
Copy code
node contrib/tokens/scripts/validate.mjs
Generate all platform builds:

bash
Copy code
node contrib/tokens/scripts/build.mjs
Outputs:

CSS: build/css/tokens.css, build/css/tokens.dark.css

SCSS: build/scss/_tokens.scss

TS: build/ts/tokens.ts

Dart: build/dart/tokens.dart

Merged JSON: build/json/tokens.merged.json

Run tests:

bash
Copy code
pnpm -C contrib/tokens test
Versioning
tokens.json contains "version": "x.y.z".

Bump the version when any token impacting visuals changes.

Document changes in contrib/CHANGELOG.md under Tokens.

Apps should pin by tag/commit; web can cache-bust using ?v=x.y.z.

SemVer guidance

MAJOR: Breaking renames/removals (e.g., primary-500 → brand-500)

MINOR: New tokens that don’t break existing ones

PATCH: Non-breaking value tweaks (e.g., contrast tuning)

Accessibility
Token palettes target WCAG 2.2 AA or better.

See contrib/brand/ACCESSIBILITY.md for required contrast pairs.

Dark theme tokens avoid pure black/white; prefer nuanced neutrals to reduce halos.

Conventions
Color steps: 50–900 (light → dark).

Spacing baseline: 4 px; multiply up for rhythm.

Radii: rounded, modern defaults (lg for cards, 2xl for hero).

Shadows: lighter in dark mode to avoid glow.

Contributing
Edit only the canonical JSONs under contrib/tokens/.

Run scripts/validate.mjs before opening a PR.

Include screenshots of key components (light/dark) and axe results.

Add entries to contrib/CHANGELOG.md.

License
Tokens and generated code are MIT. See contrib/LICENSING.md for details on assets elsewhere in contrib/.

