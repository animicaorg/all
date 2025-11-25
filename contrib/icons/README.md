# Animica Icons

System, product, and badge icons used across **Website, Explorer, Wallet Extension, Flutter Wallet, Studio**, and docs.

- Format: **SVG** (monochrome, currentColor where possible). Raster PNGs are generated only where required (stores/social).
- Size grid: designed on a **24×24** (system) and **48×48** (product) canvas.
- Stroke: 2px for system, 1.5–2px for product; rounded caps/joins.
- Color: default to `currentColor`. Theming comes from tokens (`--anm-color-*`).

See also:
- `contrib/icons/svgo.config.json` (optimization rules)
- `contrib/icons/scripts/make_sprite.mjs` (sprite builder)
- `contrib/icons/sprite/sprite.svg` (compiled sprite output)

---

## Structure

icons/
├─ README.md
├─ svgo.config.json
├─ system/ # 24px UI glyphs (mono)
│ ├─ add.svg
│ ├─ arrow-right.svg
│ ├─ check.svg
│ ├─ close.svg
│ ├─ copy.svg
│ ├─ download.svg
│ ├─ external-link.svg
│ ├─ info.svg
│ ├─ warning.svg
│ ├─ error.svg
│ └─ refresh.svg
├─ product/ # product feature glyphs (24–48px, mono)
│ ├─ poies.svg
│ ├─ ai.svg
│ ├─ quantum.svg
│ ├─ da.svg
│ ├─ vm.svg
│ └─ pq.svg
├─ badges/
│ ├─ appstore.svg
│ └─ googleplay.svg
├─ sprite/ # build output
│ └─ sprite.svg
└─ scripts/
└─ make_sprite.mjs

php-template
Copy code

---

## Usage (Inline SVG)

Inline when you need CSS control or accessibility:

```html
<!-- System icon (inherits currentColor) -->
<svg class="icon" width="24" height="24" aria-hidden="true">
  <use href="/contrib/icons/sprite/sprite.svg#check"></use>
</svg>
css
Copy code
.icon { display:inline-block; vertical-align:middle; }
.button-primary .icon { color: var(--anm-color-primary-600); }
Usage (React)
tsx
Copy code
export function CheckIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg width="24" height="24" aria-hidden="true" {...props}>
      <use href="/contrib/icons/sprite/sprite.svg#check" />
    </svg>
  );
}
Sprite Build
The sprite packs system/ SVGs (IDs based on filename). Requires Node ≥ 18.

bash
Copy code
node contrib/icons/scripts/make_sprite.mjs
# → writes contrib/icons/sprite/sprite.svg
Note: The sprite intentionally excludes product/ and badges/ by default to keep it small; reference those inline or add to the builder if needed.

SVGO (Optimization)
All icons should be run through SVGO using svgo.config.json.

One-off optimize
bash
Copy code
npx svgo -f contrib/icons/system -c contrib/icons/svgo.config.json
npx svgo -f contrib/icons/product -c contrib/icons/svgo.config.json
Authoring Rules
Canvas: 24×24 for system. Keep shapes aligned to the pixel grid.

Strokes: 2px, stroke-linecap="round" and stroke-linejoin="round".

Colors: stroke="currentColor" or fill="currentColor". No hard-coded brand hex.

Bounds: Leave 1px padding; avoid touching canvas edges unless intentional.

Accessibility: Icons are decorative by default (aria-hidden="true"). If conveying meaning, add <title> and role="img".

Naming Conventions
kebab-case for filenames: arrow-right.svg, external-link.svg.

Use consistent metaphors (info, warning, error).

Prefer universal symbols over text.

Testing
Visual check against light/dark tokens.

Ensure crisp rendering at 1× and 2× scale.

Run lint (SVGO) and rebuild sprite before committing.

License
Icons © Animica. Internal use across Animica apps permitted. External use requires permission. System glyphs derived from common metaphors and are distributed with no trademark claim.

