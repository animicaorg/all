# Animica Contrib — Licensing & Attributions

This document covers **icons, fonts, images, motion/3D assets, and templates** contained in `contrib/`.  
All code in this folder (scripts, small helpers) is MIT unless noted. Large binaries are tracked with Git LFS.

---

## Overview

| Category         | Location                         | License (SPDX)              | Notes / Attribution Rules |
|------------------|----------------------------------|-----------------------------|---------------------------|
| **Design tokens**| `contrib/tokens/*`               | MIT                         | Generated artifacts are MIT. |
| **CSS/SCSS/TS**  | `contrib/tokens/build/*`         | MIT                         | Bundled, cache-busted in apps. |
| **Icons (system)** | `contrib/icons/system/*.svg`   | MIT                         | Keep `viewBox`, no raster edits; do not remove metadata comments. |
| **Icons (product)**| `contrib/icons/product/*.svg`  | MIT                         | Same as above. |
| **Logo/wordmark**| `contrib/logos/*.svg`, `png/*`   | © Animica, **All Rights Reserved** | Use per **brand/BRAND_GUIDE.md** only; no modification without approval. |
| **Typography**   | `contrib/typography/web/*`       | **SIL OFL-1.1**             | Inter variable fonts. See `contrib/typography/licenses/INTER_LICENSE.txt`. |
| **Illustrations**| `contrib/illustrations/**/*`     | © Animica, **All Rights Reserved** | Unless a specific file includes its own license header. |
| **Patterns**     | `contrib/illustrations/patterns/*` | MIT (unless noted)         | Minor derivatives permitted with attribution. |
| **Motion (Lottie/SVG)** | `contrib/motion/**/*`     | MIT (unless noted)          | JSON/SVG animations. If sourced externally, see per-file headers. |
| **3D assets**    | `contrib/3d/**/*`                | © Animica, **All Rights Reserved** (textures: CC0 if noted) | Check individual `textures/*` headers. |
| **Email templates** | `contrib/email/**/*`          | MIT                         | Exported HTML may be redistributed with notices intact. |
| **Press kit**    | `contrib/press/**/*`             | © Animica, **All Rights Reserved** | Media usage allowed for coverage with credit “Animica”. |
| **Social assets**| `contrib/social/**/*`            | © Animica, **All Rights Reserved** | Editable files not to be redistributed as templates. |

> If a file includes a **license header block** at the top, that header takes precedence.

---

## Fonts

- **Inter** (Variable + Italic): Licensed under **SIL Open Font License 1.1**.  
  - Files: `contrib/typography/web/inter/Inter-Variable.woff2`, `Inter-Italic.woff2`  
  - Full text: `contrib/typography/licenses/INTER_LICENSE.txt`  
  - OFL summary: Free to use, modify, embed, and redistribute; **cannot** sell the font alone; **rename required** if publishing a modified version.

If you add other fonts, include their license text under `contrib/typography/licenses/` and reference them here.

---

## Icons

- **System/Product icons** in `contrib/icons` are MIT unless a specific icon states otherwise in an inline comment.
- Keep vectors clean:
  - Maintain `viewBox`, avoid hard-coded `fill` when not necessary.
  - Run `svgo` per `contrib/icons/svgo.config.json` before committing.
- If you import third-party icons, place their license file alongside and add an entry below.

**Third-party icon acknowledgments (examples/placeholders):**
- _None currently._ (Add rows as needed.)

---

## Logos & Wordmarks

- Files in `contrib/logos/*` are **copyright Animica** and not open-licensed.
- Permitted uses:
  - Animica websites, apps, docs, press, third-party articles discussing Animica (with credit).
- Prohibited:
  - Altering shapes, colors, proportions; creating confusingly similar marks.
- See `contrib/brand/BRAND_GUIDE.md` and `contrib/brand/BRAND_DONTs.md`.

---

## Illustrations, Motion, 3D

- Unless a specific artwork includes an open license header, treat as **All Rights Reserved** to Animica.
- Some **textures** in `contrib/3d/textures/*` may be **CC0**; if so, the filename or a sibling `LICENSE.txt` will state this.
- **Lottie** JSON and **motion SVG** are MIT unless noted in-file.

---

## Email Templates

- MJML sources and built HTML are MIT.  
- If you insert third-party snippets (e.g., tracking pixels, icon sets), you must comply with their terms and add them to **Attributions**.

---

## Press & Social

- Images in `contrib/press/photos/*` and `contrib/social/*` are **copyright Animica**.
- Press may reproduce photos and logos for the purpose of coverage with credit: **“Courtesy of Animica”**.

---

## Attributions (Additions Log)

Maintain this section as assets are added.

| Component / File                                   | Source / Author            | License           | Required Credit Text |
|----------------------------------------------------|----------------------------|-------------------|----------------------|
| _example: textures/orb-normal.png_                 | _Your Name_                | CC0-1.0           | _None required_      |
| _example: motion/loading.orb.json_                  | Animica                    | MIT               | “© Animica” (optional) |

---

## SPDX Headers & Provenance

- For text-based assets (SVG, CSS, JSON), include an SPDX header comment where possible:  
  `<!-- SPDX-License-Identifier: MIT -->` or `/* SPDX-License-Identifier: MIT */`.
- For binaries, ensure an adjacent `LICENSE.txt` or entry in this doc.
- CI can validate SPDX presence in SVGs and CSS (optional).

---

## Questions / Requests

For usage beyond the above (e.g., co-branding, merchandise), contact **brand@animica.dev**.  
Include intended use, distribution scope, and timeline.

