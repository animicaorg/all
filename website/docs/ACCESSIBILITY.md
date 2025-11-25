# Accessibility Guide (a11y)

This site aims to meet **WCAG 2.2 AA**. Use this checklist during implementation and PR review. Keep UX inclusive by default: keyboard-first, screen-reader friendly, and color-contrast safe.

---

## 1) Quick Checklist (PR-ready)

**Structure & semantics**
- [ ] One `<h1>` per page; headings are hierarchical (no jumps).
- [ ] Landmarks present: `<header>`, `<nav>`, `<main id="main">`, `<footer>`.
- [ ] Links vs buttons: navigation uses `<a>`; actions use `<button>`.
- [ ] Lists/quotes/tables use semantic elements (no `<div>` soup).

**Keyboard**
- [ ] All interactive elements reachable with **Tab / Shift+Tab**.
- [ ] Visible **focus** on every focusable element (`:focus-visible` styles).
- [ ] No keyboard traps; Escape closes modals/menus.
- [ ] Skip link appears on focus and jumps to `#main`.

**Forms**
- [ ] Every input has a `<label>` or `aria-label`/`aria-labelledby`.
- [ ] Errors announced (live region), associated via `aria-describedby`.
- [ ] Grouped controls (e.g., radios) wrapped in `<fieldset><legend>`.

**Media & images**
- [ ] Informative images/SVGs have meaningful `alt` or `<title>`.
- [ ] Decorative images have `alt=""` and `aria-hidden="true"`.
- [ ] Captions/transcripts for videos; use `controls`.

**ARIA**
- [ ] Use ARIA only to **add** semantics, never to replace semantics.
- [ ] `role="dialog"` + `aria-modal="true"` + labelled dialog title.
- [ ] Dynamic status uses `aria-live="polite"` or `assertive"` sparingly.

**Motion & timing**
- [ ] Respect `prefers-reduced-motion`; avoid parallax/auto-animations.
- [ ] No content auto-updates faster than 3s without user control.

**Color & contrast**
- [ ] Text contrast ≥ **4.5:1** (normal), **3:1** (≥ 24px or 700+ / ≥ 18.66px).
- [ ] Icons/controls (non-text) contrast ≥ **3:1** against adjacent colors.
- [ ] Focus ring contrast ≥ **3:1** vs both rest & focused bg.

**Announcements**
- [ ] Loading/ticker/status uses live region; do not spam screen readers.

---

## 2) Color & Contrast Tokens

Our design tokens live in `src/styles/tokens.css` and theme overrides in `src/styles/theme.css`. Use tokens—not raw hex—to keep contrast consistent across light/dark.

### 2.1 Core tokens (reference)
- `--color-bg` / `--color-bg-muted`  
- `--color-fg` / `--color-fg-muted`
- `--color-primary` / `--color-primary-contrast`
- `--color-accent` / `--color-accent-contrast`
- `--color-success`, `--color-warning`, `--color-danger`
- `--focus-ring` (focus outline color)

> **Rule:** When placing text on a brand color, use the paired `*-contrast` token for text/foreground.

### 2.2 Required minimums
| Usage                              | Foreground token                | Background token          | Min contrast |
|-----------------------------------|---------------------------------|---------------------------|--------------|
| Body text / lists / cards         | `--color-fg`                    | `--color-bg`              | 4.5:1        |
| Secondary text                    | `--color-fg-muted`              | `--color-bg`              | 4.5:1        |
| Primary button label              | `--color-primary-contrast`      | `--color-primary`         | 4.5:1        |
| Outline button label              | `--color-fg`                    | `--color-bg`              | 4.5:1        |
| Badges/chips text                 | `--color-*-contrast`            | `--color-*`               | 4.5:1        |
| Focus ring vs surroundings        | `--focus-ring`                  | adjacent bg/element       | 3:1          |
| Icons/controls (no text)          | icon color                      | adjacent bg               | 3:1          |

### 2.3 Example utilities
```css
/* Focus ring defaults */
:where(a, button, input, [tabindex]) {
  outline: none;
}
:where(a, button, input, [tabindex]):focus-visible {
  outline: 3px solid var(--focus-ring);
  outline-offset: 2px;
}

/* High-contrast text on primary surfaces */
.btn-primary {
  color: var(--color-primary-contrast);
  background: var(--color-primary);
}

/* Muted text still meets 4.5:1; verify token pairs in both themes */
.text-muted {
  color: var(--color-fg-muted);
}


⸻

3) Skip Link (include in layout)

Add near top of the document (hidden until focused):

---
// In `src/layouts/BaseLayout.astro`
---
<a class="skip-link" href="#main">Skip to content</a>
<style>
.skip-link {
  position: absolute; left: 0.5rem; top: -100rem;
  background: var(--color-bg); color: var(--color-fg);
  padding: .5rem .75rem; border: 2px solid var(--focus-ring); border-radius: .375rem;
}
.skip-link:focus { top: .5rem; z-index: 1000; }
</style>

<html lang="en">
  <body>
    <header>...</header>
    <main id="main">
      <slot />
    </main>
    <footer>...</footer>
  </body>
</html>


⸻

4) Forms: Labels, Errors, Live Regions

<form aria-describedby="form-status">
  <label for="email">Email</label>
  <input id="email" type="email" name="email" autocomplete="email" required />
  <p id="email-hint" class="hint">We'll never share your email.</p>

  <button type="submit">Subscribe</button>

  <!-- Status updates announced to AT -->
  <div id="form-status" aria-live="polite"></div>
</form>

	•	Associate errors with inputs via aria-describedby="email-error".
	•	Keep messages short and specific.

⸻

5) Modals & Dialogs (essentials)
	•	role="dialog" + aria-modal="true".
	•	Provide a heading; connect via aria-labelledby.
	•	Trap focus within the dialog and restore focus when closed.
	•	Dismiss with Escape & close button.
	•	Inert page behind (e.g., inert attribute or aria-hidden + overlay).

<div class="overlay" hidden></div>
<div role="dialog" aria-modal="true" aria-labelledby="dlg-title" hidden>
  <h2 id="dlg-title">Confirm action</h2>
  <p>Are you sure?</p>
  <button data-close>Cancel</button>
  <button data-confirm>Confirm</button>
</div>

Use a well-tested dialog utility if available; DIY focus traps are easy to get wrong.

⸻

6) Tables
	•	Use <th scope="col">/<th scope="row"> for simple tables.
	•	For complex tables, use headers/id associations.
	•	Provide caption describing the table purpose.

<table>
  <caption>Latest Blocks</caption>
  <thead>
    <tr><th scope="col">Height</th><th scope="col">Hash</th><th scope="col">Txs</th></tr>
  </thead>
  <tbody>
    <tr><th scope="row">123</th><td>0xabc…</td><td>14</td></tr>
  </tbody>
</table>


⸻

7) SVGs & Icons
	•	Informative icon: include a <title> and aria-labelledby.
	•	Decorative icon: aria-hidden="true" and no <title> to avoid noise.

<svg width="24" height="24" aria-hidden="true"><use href="#icon-check"/></svg>


⸻

8) Motion Preferences

@media (prefers-reduced-motion: reduce) {
  * { animation-duration: 0.001ms !important; animation-iteration-count: 1 !important; transition-duration: 0.001ms !important; }
}

Avoid auto-scrolling and looping animations without user intent.

⸻

9) Live Data (Status/Ticker)
	•	Use aria-live="polite" for periodic status updates.
	•	Debounce announcements; don’t announce every frame/tick.
	•	Provide a manual “Refresh” action for users who prefer it.

⸻

10) Testing & Tooling

Automated
	•	axe-core / @axe-core/playwright: unit & E2E a11y assertions.
	•	Lighthouse (CI & local).
	•	eslint-plugin-jsx-a11y (if using JSX components).

Manual
	•	Keyboard-only pass (Tab/Shift+Tab/Enter/Escape/Space).
	•	Screen readers: VoiceOver (macOS), NVDA (Windows), TalkBack (Android).
	•	High contrast modes & zoom at 200%.

Example Playwright + axe snippet:

import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('homepage is accessible', async ({ page }) => {
  await page.goto('/');
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});


⸻

11) Content Guidelines
	•	Clear link text: avoid “click here”; prefer “Read Docs” → destination known.
	•	Headings summarize sections; avoid clever but vague titles.
	•	Never rely on color alone to convey meaning (add icons/labels/ARIA).

⸻

12) Governance
	•	Treat a11y regressions as bugs.
	•	Block PRs with critical axe/Lighthouse violations.
	•	Include a11y notes in design reviews and QA checklists.

⸻

13) Resources
	•	WCAG 2.2 (AA), WAI-ARIA Authoring Practices, Inclusive Components
	•	Accessibility Insights, axe DevTools, MDN ARIA/HTML semantics

Make it usable for everyone. It’s the right thing and good product practice. ♿️
