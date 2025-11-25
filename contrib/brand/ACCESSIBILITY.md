# Animica — Accessibility (A11y) Guide

This guide defines **contrast pairs** and **accessibility rules** for Animica products (Web, Explorer, Wallet, Extension, Docs, Email). It complements the Brand Guide and design tokens.

---

## 1) Principles

- **Perceivable** — Color contrast and alternatives for non-text content.
- **Operable** — Fully keyboard accessible with visible focus.
- **Understandable** — Clear labels, consistent patterns, error guidance.
- **Robust** — Compatible with assistive tech; semantic structures.

References: WCAG 2.2 AA (target), AAA where feasible for small UI text.

---

## 2) Contrast Pairs (Tokens)

Minimums:
- Body text & icons: **AA** (≥ 4.5:1) below 18 px / 1.4 em; **AAA** preferred for small UI labels.
- Large text (≥ 24 px regular or ≥ 18.66 px bold): **AA** (≥ 3.0:1).
- Non-text UI elements (icons, strokes): **AA** guidance (≥ 3.0:1).

Use tokens instead of raw hex; verify with design-tool plugins or `axe`/`jest-axe`.

### Light Theme (examples)

| Usage                | Foreground token                | Background token                | Target Ratio |
|---------------------|----------------------------------|----------------------------------|--------------|
| Body text           | `--anm-color-neutral-900`        | `--anm-color-neutral-50`         | ≥ 7.0:1      |
| Subtext / meta      | `--anm-color-neutral-700`        | `--anm-color-neutral-50`         | ≥ 4.5:1      |
| Primary button text | `--anm-color-surface-0`          | `--anm-color-primary-600`        | ≥ 7.0:1      |
| Ghost button text   | `--anm-color-primary-700`        | `--anm-color-surface-0`          | ≥ 4.5:1      |
| Link                | `--anm-color-primary-700`        | `--anm-color-neutral-50`         | ≥ 4.5:1      |
| Error text          | `--anm-color-error-700`          | `--anm-color-neutral-50`         | ≥ 4.5:1      |
| Divider             | `--anm-color-neutral-300`        | `--anm-color-neutral-50`         | n/a (decor.) |

### Dark Theme (examples)

| Usage                | Foreground token                 | Background token                 | Target Ratio |
|---------------------|-----------------------------------|-----------------------------------|--------------|
| Body text           | `--anm-color-neutral-50`          | `--anm-color-surface-900`         | ≥ 12.0:1     |
| Subtext / meta      | `--anm-color-neutral-300`         | `--anm-color-surface-900`         | ≥ 4.5:1      |
| Primary button text | `--anm-color-surface-0`           | `--anm-color-primary-600`         | ≥ 7.0:1      |
| Link                | `--anm-color-primary-400`         | `--anm-color-surface-900`         | ≥ 4.5:1      |
| Error text          | `--anm-color-error-400`           | `--anm-color-surface-900`         | ≥ 4.5:1      |

> Validate real ratios against the current token palette in `contrib/tokens/*.json`.

---

## 3) Focus & Interaction

- **Focus visible:** Always show a focus indicator (≥ 3:1 against adjacent colors). Example CSS:

```css
:focus-visible {
  outline: 2px solid var(--anm-color-primary-600);
  outline-offset: 2px;
}
Hit targets: Minimum 44×44 px for touch.

States: Provide :hover, :focus, :active, and :disabled. Do not rely on color alone—add underline or icon where meaningful.

Pointer traps: Avoid locking scroll/focus; modals must trap focus and restore it on close.

4) Keyboard Support
Full navigation without a mouse: Tab/Shift+Tab, Arrow keys within menus, Enter/Space to activate, Esc to dismiss.

Skip links: Provide Skip to content as the first focusable element.

Components:

Menu / Select: ARIA roles menu, menuitem, listbox, option as appropriate.

Dialog: role="dialog" with aria-modal="true" and labelled by a heading.

5) Semantics & ARIA
Prefer semantic HTML (buttons, links, headings, lists) before ARIA.

Landmarks: header, nav, main, aside, footer per page.

Form labels: <label for> or aria-label/aria-labelledby (not placeholder-only).

Tables: <th scope="col|row">, <caption> for context; associate sort state with aria-sort.

6) Forms & Errors
Inline validation with clear messages (what failed, how to fix).

Programmatic error linking via aria-describedby.

Timing: Avoid timeouts; if necessary, provide extend/turn-off options.

Example:

html
Copy code
<label for="addr">Address</label>
<input id="addr" aria-describedby="addrHelp addrErr">
<div id="addrHelp">Bech32m, hrp <code>am</code>.</div>
<div id="addrErr" role="alert" hidden>Invalid address format.</div>
7) Motion & Reduced Motion
Respect prefers-reduced-motion: reduce; provide non-animated fallbacks.

Avoid parallax/auto-anim on critical paths (onboarding, transactions).

Keep essential motion ≤ 200–300 ms; avoid infinite animations in reading flows.

css
Copy code
@media (prefers-reduced-motion: reduce) {
  * { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
}
8) Charts & Data Viz (Explorer)
Provide textual summaries and aria-labels for charts.

Use color + pattern/shape for series; ensure ≥ 3:1 contrast against background.

Keyboard panning/zooming: arrows/PageUp/PageDown with visible focus.

Tooltips must be reachable (focusable) or mirrored in a table.

9) Email Templates
Inline CSS; avoid background images for critical text.

Dark-mode friendly: test in clients that support prefers-color-scheme.

Use semantic table markup with headings; provide alt text for images.

Minimum 14px body text; buttons with adequate padding and contrast.

10) Media & Alt Text
Images: alt describes the purpose (“Animica logo”), not “image of”.

Decorative images: alt="" and role="presentation".

Video: Captions/subtitles; transcripts for long-form content.

11) Testing & Tooling
Automated:

axe-core, jest-axe for unit/integration.

Playwright/Cypress with keyboard flows.

Lighthouse a11y scores in CI (budget ≥ 95).

Manual:

Screen reader smoke tests (VoiceOver/NVDA).

High-contrast mode checks.

Zoom to 200%/400% — layout must remain operable.

12) Component Checklists
Buttons

 4.5:1 text contrast

 Focus visible

 Disabled state non-focusable

 Icon-only has aria-label

Forms

 Explicit labels

 Helpful errors and recovery

 Logical tab order

 Status communicated with role="status" or aria-live

Modal

 Focus trapped & restored

 Escape closes

 Labeled by a heading

13) Reporting & Exceptions
Log a11y issues as severity-P1 bugs.

Exceptions require: rationale, impacted users, mitigation, timeline to fix.

14) Changelog
Track changes in contrib/CHANGELOG.md under Accessibility.

