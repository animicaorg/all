---
title: Cookie & Storage Policy
description: How Animica uses cookies, local storage, and similar technologies, and how you can control them.
lastUpdated: 2025-10-09
---

# Cookie & Storage Policy

**Effective date:** October 9, 2025

This Cookie & Storage Policy explains how **Animica Labs** (“**Animica**”, “**we**”, “**us**”) uses browser cookies,
localStorage, sessionStorage, and similar technologies on the Animica website, Studio Web, Explorer Web, and related pages (the **Sites**).
For how we process personal data generally, see our **[Privacy Policy](/content/legal/privacy)**.

We aim to use the **minimum necessary** client-side storage to provide core features, security, and performance.  
Advertising trackers are **not** used.

---

## 1) What are cookies & similar tech?

- **Cookies** are small text files stored by your browser.  
- **LocalStorage / SessionStorage** are key-value stores in your browser used by web apps.  
- **IndexedDB / Cache Storage** may be used for performance (e.g., caching docs or compiled artifacts in Studio).

---

## 2) Why we use them

We categorize our usage as follows:

### a) Strictly necessary (essential)
Used to deliver the Sites and keep them secure. For example:
- Session state (e.g., current locale, theme, or network selection),
- CSRF tokens and anti-abuse rate-limit hints,
- Feature flags to ensure compatible UI behavior.

These are **required** for the Sites to function and cannot be switched off via our banner.

### b) Functional (preferences)
Improve your experience but are optional. For example:
- Remembering last opened project in **Studio**, editor settings (tabs, font size),
- Remembering RPC endpoint selections or address-book aliases in **Explorer**.

### c) Performance & analytics (opt-in)
Privacy-respecting analytics (e.g., page views, load times) used to improve reliability and usability.
We prefer tools that avoid tracking cookies and IP retention. These are **disabled by default** unless you **opt in**.

> We do not use third-party advertising cookies, cross-site behavioral ads, or social media pixels.

---

## 3) Examples of keys we set

> Names are illustrative and may change with releases.

| Scope     | Storage        | Key / Cookie Name                 | Purpose                                 | Expires           |
|-----------|----------------|-----------------------------------|-----------------------------------------|-------------------|
| Site      | cookie         | `animica_csrf`                    | CSRF protection                         | Session           |
| Site      | localStorage   | `animica:theme`                   | Light/Dark preference                    | 1 year (rolling)  |
| Site      | localStorage   | `animica:analytics:consent`       | Records opt-in/out                       | 1 year (rolling)  |
| Explorer  | localStorage   | `animica:rpc:url`                 | Preferred RPC endpoint                   | 90 days           |
| Studio    | localStorage   | `studio:lastProject`              | Reopen last project                      | 90 days           |
| Studio    | indexedDB      | `studio:cache:*`                  | Cached compiler output/assets            | Until cleared     |

---

## 4) Your choices

### a) Consent banner / settings
On first visit, we present a minimal consent choice for analytics. You can change this anytime via **Settings → Privacy** on the Site footer or the in-app menu.

- **Accept** analytics → enables optional measurement.
- **Decline** analytics → remains off; only essential storage is used.

### b) Browser controls
You can:
- Block or delete cookies at the browser level,
- Use “Do Not Track” or tracking prevention features,
- Clear site data (cookies, storage, cached files).

Blocking **essential** cookies may break functionality (logins, forms, CSRF, or persisted preferences).

### c) Platform-specific toggles
Studio/Explorer may offer per-feature toggles (e.g., “remember RPC endpoint”). Turning these off limits local persistence.

---

## 5) Third-party services

We may embed privacy-respecting analytics or uptime monitors. Where technically possible we configure:
- No cross-site tracking,
- Anonymized IP or no IP retention,
- No advertising cookies.

Links to third-party policies will be surfaced in the Settings panel when those services are active.

---

## 6) Data retention

- **Essential cookies**: kept only as long as needed to operate the Sites.
- **Preferences**: retained until you delete them or they expire.
- **Analytics (if enabled)**: retained per-tool defaults or shorter; we prefer aggregated, non-identifying reports.

---

## 7) Changes to this policy

We may update this policy to reflect product or legal changes. The “Effective date” will be updated and material changes may include an additional notice (e.g., banner).

---

## 8) Contact

Questions about cookies or site storage?  
**privacy@animica.dev**

