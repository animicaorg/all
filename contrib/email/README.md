# Email templates & build pipeline

Production-ready email assets for Animica announcements, releases, security notices, and transactional receipts.

> Related: `contrib/LICENSING.md` (asset licenses), `contrib/tokens/*` (brand tokens), and `contrib/email/scripts/build.mjs` (builder).

---

## What’s here

contrib/email/
├─ templates/
│ ├─ newsletter.mjml # long-form news / announcements
│ ├─ release-notes.mjml # product changes, highlights
│ ├─ security-advisory.mjml # security notices / urgent comms
│ └─ transaction-receipt.html # prebuilt HTML transactional sample
├─ assets/
│ └─ logo.png # brand mark used by templates
└─ scripts/
└─ build.mjs # mjml → html (inlines CSS, validates)

yaml
Copy code

- **MJML** is used for responsive markup and cross-client compatibility.
- **transaction-receipt.html** is provided as plain HTML for systems that cannot run MJML.

---

## Build

The builder compiles MJML → HTML, inlines CSS, validates sizes, and writes to `dist/`.

```bash
# from repo root (requires Node 18+)
node contrib/email/scripts/build.mjs
Outputs

dist/newsletter.html

dist/release-notes.html

dist/security-advisory.html

Flags (optional):

bash
Copy code
# dry-run (print to stdout only)
DRY_RUN=1 node contrib/email/scripts/build.mjs

# base URL for images/links in prod
BASE_URL=https://assets.animica.dev node contrib/email/scripts/build.mjs

# fail build if image fetch or inline size exceeds 100KB
STRICT=1 node contrib/email/scripts/build.mjs
The builder attempts to:

Inline small images as data: when beneficial (fallback to absolute URLs).

Normalize to UTF-8, sRGB, and strip metadata.

Warn on content wider than 600px and long lines that affect Gmail clipping.

Using templates
Variables
Templates are authored with simple Mustache-style placeholders: {{title}}, {{cta_url}}, {{user_name}}, etc.
Your sending service is expected to merge values before sending.

Example payload:

json
Copy code
{
  "template": "newsletter",
  "vars": {
    "title": "Animica — October highlights",
    "subtitle": "Deterministic VM, DA upgrades, and more",
    "cta_label": "Read the blog",
    "cta_url": "https://animica.dev/blog/october",
    "footer_address": "Animica Labs · 123 Web3 Ave · Internet",
    "unsubscribe_url": "https://animica.dev/unsubscribe?u={{user_id}}"
  }
}
If your mailer lacks templating, you can pre-render server-side (e.g., Mustache/Handlebars) and send the final HTML.

Content guidelines
Width: keep the main container to 600px for broad client support.

Fonts: rely on system fonts; avoid webfont downloads (blocked by many clients).

Buttons: use bulletproof buttons (<table>-based) from MJML components.

Images: provide width/height attributes and meaningful alt text.

Links: use absolute URLs; avoid tracking parameters in security-sensitive mails.

Dark mode: prefer explicit colors with sufficient contrast; verify both modes.

Deliverability checklist
SPF/DKIM/DMARC configured for the sending domain.

From name & address are consistent (e.g., Animica <hello@animica.dev>).

List-Unsubscribe header + visible unsubscribe link in bulk mail.

Physical address in footer (CAN-SPAM/GDPR friendly).

Avoid image-only emails; include readable text content.

Keep HTML payloads under ~100KB to avoid Gmail clipping.

Accessibility
Maintain 4.5:1 contrast for body text, 3:1 for large text.

Provide role="presentation" on layout tables; use semantic headings.

All actionable links must be keyboard reachable and have clear labels.

Localization (optional)
Mirror strings in an i18n layer and keep template structure identical.

Validate encoded characters render correctly across clients (use UTF-8).

Testing
Local preview: open dist/*.html in a browser for a quick pass.

Sanity checks in CI:

max width, inline CSS present, alt attributes present.

no external fonts, data URIs under size threshold.

Full client tests (optional): Litmus/Email on Acid before major sends.

Updating the logo
Replace assets/logo.png (recommended 240×48 or similar @2x).

Ensure a solid or transparent background depending on the template theme.

Keep under 40KB if possible.

Security advisory template
Use security-advisory.mjml for urgent communications:

Put the CVE or advisory ID in the subject and header.

Avoid tracking pixels or heavy images.

Link to a canonical post with signatures/checksums where applicable.

Transactional receipts
transaction-receipt.html is a reference layout for purchase/transfer receipts. Integrate with your backend renderer and replace token fields (e.g., amount, tx hash, address). Keep totals prominent and link to the Explorer page for the transaction.

Versioning & caching
When templates change, bump an entry in contrib/CHANGELOG.md.

If served via a CDN for previews, use immutable caching with a versioned path/query.

License
Email templates are © Animica. Internal use across Animica products is permitted. External/commercial reuse requires written permission. See contrib/LICENSING.md for details.
