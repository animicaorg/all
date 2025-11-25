# Docs Templates

Reusable authoring templates for long-form docs (whitepapers), press releases, and slide decks. These files mirror our brand tokens and typography, so you can produce consistent artifacts quickly.

> Related: `contrib/brand/*`, `contrib/tokens/*`, `contrib/press/*`, and `contrib/email/*`.

---

## What’s included

contrib/docs-templates/
├─ whitepaper.docx # Word template (.docx)
├─ whitepaper.key # Keynote template (light theme)
├─ whitepaper.potx # PowerPoint template (master theme)
├─ press-release.docx # Press release boilerplate
└─ slide-deck/
├─ Animica-Light.key # Keynote deck (light)
├─ Animica-Dark.key # Keynote deck (dark)
└─ Animica-Pitch.pptx # PPTX pitch deck

yaml
Copy code

**Why both Keynote and PowerPoint?**  
Keynote is our “source of truth” for marketing visuals. PPTX is provided for vendor compatibility.

---

## Brand & typography

- **Colors** come from `contrib/tokens/tokens.json` and are baked into master styles.
  - Primary: `#0B0D12` (ink), Accent: `#5EEAD4` (mint), Surfaces: `#FFFFFF/#0F172A`.
- **Fonts:** Inter family. If Inter is not installed, templates fall back to system UI fonts.
  - Web export will substitute automatically; for print/PDF, install Inter to avoid reflow.
- **Grid:** 12-column with 80–96px margins (deck) and 40–56px margins (docs).

---

## Using the templates

### Whitepaper (Word / Keynote)
1. Open **`whitepaper.docx`** (or **`whitepaper.key`** if you prefer Keynote).
2. Replace cover fields:
   - **Title**, **Subtitle**, **Version**, **Date**, **Authors**.
3. Use the built-in **Styles**/Text Styles (Heading 1/2/3, Body, Caption, Code).
4. Export:
   - **PDF (print)**: A4 or US Letter per target audience.
   - **Web PDF**: Optimize for ~1–5 MB, sRGB, embedded fonts.

### Press release (Word)
1. Open **`press-release.docx`** and fill in `[CITY, STATE]`, `[DATE]`, and boilerplate fields.
2. Keep body between **400–700 words**; include a single call-to-action link.
3. Export to **.docx** (editorial) and **PDF** (distribution).

### Slide decks
- **Keynote (Light/Dark):**
  - Use provided **Master Slides** (Title, Section, Two-col, Quote, Gallery, Appendix).
  - Stick to **24–40pt** body text and **≥ 4.5:1** contrast for accessibility.
- **Pitch (PPTX):**
  - Brand theme preloaded. Avoid adding ad-hoc colors; use theme swatches only.

---

## Content guidelines

- **Voice & tone:** See `contrib/brand/VOICE_AND_TONE.md`.
- **Acronyms:** Expand on first use. Keep a glossary appendix for technical docs.
- **Figures:** Export SVG/PNG at 2×; limit PNGs to ≤ 300 KB when possible.
- **Code:** Use the “Code” style; avoid screenshots for copyable snippets.
- **Citations:** Use endnotes or a References section; hot-link canonical sources.

---

## Accessibility checklist

- Headings follow a logical order (no level jumps).
- All images have meaningful **alt text** (or are marked decorative).
- Text contrast meets **WCAG AA** (≥ 4.5:1 normal, 3:1 large).
- Do not rely on color alone to convey meaning (use icons/labels).

---

## Export presets

**PDF (for web)**
- sRGB, downsample images to 144–220 DPI.
- Embed fonts (subset Inter).
- Target size: **≤ 5 MB** for long whitepapers, **≤ 2 MB** for press PDFs.

**Slides → PDF**
- “Best” image quality for conference screens; reduce to 150–200 DPI for email.

---

## Re-branding / updating assets

- Logo files live in `contrib/logos/*`. Swap there **first**, then re-apply in:
  - Keynote: Document > Replace Image (on master).
  - PowerPoint: View > Slide Master > Replace Picture.
  - Word: Design > Watermark/Headers > Replace.
- Token updates:
  - Adjust `contrib/tokens/tokens.json`.
  - Rebuild downstream bundles (`contrib/tokens/scripts/build.mjs`) if you’re using any doc automation.
  - Manually update theme colors in Keynote/PPTX (office files do not read JSON tokens).

---

## Versioning

- Bump `contrib/CHANGELOG.md` with notable template changes (e.g., new cover, grid tweaks).
- Keep a **version** on the whitepaper cover (e.g., v1.2.0) and update footer date on export.

---

## Legal

- Templates are © Animica. Internal use allowed across Animica products.
- External/commercial reuse requires written permission.  
  See `contrib/LICENSING.md` for full details.

---

## Tips

- Prefer **vector** figures (SVG → PNG fallback for office apps).
- For dark mode decks, avoid thin mint strokes on dark backgrounds—use **2–3px** minimum.
- Keep slide titles short (≤ 60 chars). One idea per slide.
- For press, include **boilerplate** from `contrib/press/boilerplate.txt` and link to the **press kit**.

---

## Troubleshooting

- **Fonts reflow in PDFs:** Install Inter; export with “Embed fonts” enabled.
- **Colors look dull:** Ensure sRGB profile on export.
- **Huge PDF size:** Re-export images at 2×; avoid embedding videos; compress to 144–220 DPI.

---

Happy writing.
