# Animica — Brand Don’ts (Misuse Examples)

These rules prevent dilution of the Animica brand. When in doubt, use the assets and colors exactly as provided in `contrib/logos/` and `contrib/tokens/`.

> TL;DR: **Don’t alter shapes, colors, proportions, or spacing.** Don’t place the logo on low-contrast or noisy backgrounds without a keyline.

---

## 1) Color & Effects

❌ **Do not** recolor the logo outside the brand palette.  
❌ **Do not** apply gradients, glows, drop shadows, bevels, or 3D effects.  
❌ **Do not** overlay imagery or textures inside the glyphs.

✅ Use:
- `contrib/logos/animica-logo.svg` (light backgrounds)
- `contrib/logos/animica-logo-dark.svg` (dark backgrounds)

---

## 2) Proportions & Geometry

❌ **Do not** stretch, squish, skew, rotate, or change aspect ratio.  
❌ **Do not** modify the mark’s geometry or letterforms.  
❌ **Do not** alter spacing between the mark and wordmark.

✅ Maintain a **fixed aspect ratio** and the provided lockup spacing.

---

## 3) Clear Space & Sizing

❌ **Do not** place other graphics or text within the red “A” clear-space (see `BRAND_GRID.svg`).  
❌ **Do not** reduce below minimum sizes (screen: 160 px width for full lockup; 48 px mark-only).

✅ Respect the **clear-space = height of “A”** and minimum sizes.

---

## 4) Backgrounds & Contrast

❌ **Do not** place the light logo on light backgrounds or the dark logo on dark backgrounds.  
❌ **Do not** place on busy photography/gradients **without** a keyline or contrast panel.  
❌ **Do not** use partially transparent logos over complex video.

✅ Use sufficient contrast (WCAG AA). Add a **1.5–2 px keyline** when overlaying imagery.

---

## 5) Typography & Lockups

❌ **Do not** replace the wordmark with other fonts.  
❌ **Do not** create new lockups (e.g., mark + partner logo) without approval.  
❌ **Do not** add taglines tightly under the logo.

✅ Use official wordmarks. For co-branding, request review via **brand@animica.dev**.

---

## 6) Iconography Misuse

❌ **Do not** use system/product icons as substitutes for the Animica mark.  
❌ **Do not** change icon stroke weights, joins, or viewBox outside the icon system rules.

✅ Keep system icons at **24×24 viewBox**, 2 px strokes, and consistent visual weight.

---

## 7) File Handling

❌ **Do not** re-export SVGs through tools that rasterize or strip viewBox/IDs.  
❌ **Do not** convert logos to low-resolution PNGs for large placements.  
❌ **Do not** commit ad-hoc exports; follow repo structure and LFS policy.

✅ Use the provided **SVGs** for scalable surfaces; PNG/ICO/ICNS only where required.

---

## 8) Tone & Claims

❌ **Do not** pair the logo with unverified claims (“fastest chain ever”, “guaranteed returns”).  
❌ **Do not** use the brand to imply endorsement or partnership without written approval.

✅ Keep copy **confident, precise, and evidence-based** (see `VOICE_AND_TONE.md`).

---

## Visual Misuse Examples (Mockups)

> Placeholders — add your own side-by-side examples in `/contrib/brand/misuse/` if needed.

- Recolored logo (non-brand gradient) — **don’t**  
- Squished aspect ratio — **don’t**  
- Logo on busy background without keyline — **don’t**  
- Custom lockup with arbitrary spacing — **don’t**

---

## Quick Checklist Before Publishing

- Uses **official** SVG from `contrib/logos/` (correct variant for light/dark).  
- Clear-space respected (see `BRAND_GRID.svg`).  
- Contrast passes WCAG AA.  
- No distortions/effects/recolors.  
- Token colors and spacing from `contrib/tokens/` used where applicable.  
- Co-branding reviewed where applicable.

For approvals or questions: **brand@animica.dev**
