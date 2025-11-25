# Website Security Guide

Harden the public site with **defense-in-depth**: strict HTTP headers, a sane Content Security Policy (CSP), careful iframe usage, and a clear “no secrets in the client” rule.

This document gives you pragmatic defaults for **Vercel**, **Netlify**, and any static CDN.

---

## 1) Threat Model (quick)

- **What we defend**: end-users against XSS, clickjacking, mixed content, data exfil; site integrity (supply chain), cookies/consent.
- **What we don't**: custodial keys (none here), privileged admin panels (not part of this site).

---

## 2) Security Headers (recommended)

| Header | Value (example) | Why |
|---|---|---|
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains; preload` | Pins HTTPS (HSTS) |
| `Content-Security-Policy` | see section below | Blocks inline/script injection; limits outbound |
| `X-Content-Type-Options` | `nosniff` | Prevents MIME sniffing |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Reduces cross-site leakage |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), fullscreen=(self)` | Sandboxes powerful APIs |
| `Cross-Origin-Embedder-Policy` | `require-corp` *(optional)* | Stricter isolation (if needed) |
| `Cross-Origin-Opener-Policy` | `same-origin` | Mitigates cross-origin leaks |
| `Cross-Origin-Resource-Policy` | `same-origin` | Resource isolation |
| `X-Frame-Options` | `DENY` *(legacy)* | Backstop for old UAs (CSP frame-ancestors is primary) |

> ⚠️ Only use **COEP/COOP** if you know your embeds still work under those constraints.

---

## 3) Content Security Policy (CSP)

Start tight; add hostnames as needed. We separate **base** and **with-embeds** variants.

### 3.1 Base CSP (no third-party embeds)

```http
Content-Security-Policy:
  default-src 'self';
  base-uri 'self';
  object-src 'none';
  frame-ancestors 'none';
  img-src 'self' data: blob:;
  font-src 'self' data:;
  style-src 'self' 'unsafe-inline';
  script-src 'self';
  connect-src 'self' https://rpc.example.tld https://api.example.tld;
  form-action 'self';
  upgrade-insecure-requests;

Notes:
	•	style-src 'unsafe-inline' is often needed for Astro/Prism inline styles. If you can, replace with hashes.
	•	Add your RPC endpoint(s) to connect-src.

3.2 With analytics (Plausible or PostHog)

Add domains only when the user opted in (via CookieBanner):

# + analytics hosts (conditional)
script-src 'self' https://plausible.io https://cdn.posthog.com;
connect-src 'self' https://rpc.example.tld https://events.posthog.com https://plausible.io;
img-src 'self' data: blob: https://plausible.io;

Your client code should only load analytics when window.animicaHasAnalyticsConsent() is true.

3.3 With video embeds (YouTube/Vimeo)

frame-ancestors 'self';
frame-src 'self' https://www.youtube-nocookie.com https://player.vimeo.com;
child-src 'self' https://www.youtube-nocookie.com https://player.vimeo.com; /* compatibility */

Keep frame-ancestors 'self' (or specific allowed parents) to prevent clickjacking.
Do not allow * here.

⸻

4) Iframe Rules

Use the minimal permissions necessary.

YouTube (privacy-enhanced):

<iframe
  src="https://www.youtube-nocookie.com/embed/VIDEO_ID"
  loading="lazy"
  referrerpolicy="strict-origin-when-cross-origin"
  sandbox="allow-scripts allow-same-origin allow-presentation"
  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
></iframe>

Vimeo:

<iframe
  src="https://player.vimeo.com/video/VIDEO_ID"
  loading="lazy"
  referrerpolicy="strict-origin-when-cross-origin"
  sandbox="allow-scripts allow-same-origin"
  allow="autoplay; picture-in-picture"
></iframe>

Guidelines:
	•	Always include sandbox and only the capabilities you need.
	•	Never use allow-top-navigation-by-user-activation unless required.
	•	Use referrerpolicy to minimize leakage.

⸻

5) No Secrets in the Client
	•	Only PUBLIC_* env vars are allowed in the client bundle (see src/env.ts).
Never put API keys, bearer tokens, or credentials in PUBLIC_* or source code.
	•	If you need secrets, host them behind serverless functions and access them server-side, not from the browser.
	•	Review .env.example—it must not include secrets; only document public values.

⸻

6) Cookies & Consent
	•	All analytics must be off by default; enable only after consent.
	•	If you ever set cookies:
	•	Secure; SameSite=Lax (or Strict), HttpOnly for server cookies.
	•	Minimize lifetime; avoid cross-site usage unless absolutely required.

⸻

7) Mixed Content & External Assets
	•	Force HTTPS via HSTS and upgrade-insecure-requests.
	•	Host fonts and icons locally when possible. If using third-party CDNs, pin versions and review licenses.

⸻

8) Supply Chain & Integrity
	•	Commit lockfiles (pnpm-lock.yaml).
	•	Pin external script versions; avoid wide-open latest.
	•	Consider Subresource Integrity (SRI) for any third-party script loaded from a CDN.

⸻

9) Caching & Privacy
	•	Static assets: long cache with immutable filenames.
	•	API routes (/api/*): consider Cache-Control: no-store if responses may include health or status that should not be cached by intermediaries.
	•	Do not log PII. Anonymize IPs if you run serverless logs or analytics.

⸻

10) Examples: Vercel & Netlify

10.1 Vercel headers (website/vercel.json)

{
  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        { "key": "Strict-Transport-Security", "value": "max-age=31536000; includeSubDomains; preload" },
        { "key": "X-Content-Type-Options", "value": "nosniff" },
        { "key": "Referrer-Policy", "value": "strict-origin-when-cross-origin" },
        { "key": "Permissions-Policy", "value": "camera=(), microphone=(), geolocation=(), fullscreen=(self)" },
        { "key": "X-Frame-Options", "value": "DENY" },
        { "key": "Content-Security-Policy",
          "value": "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'; img-src 'self' data: blob:; font-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' https://rpc.example.tld; form-action 'self'; upgrade-insecure-requests;"
        }
      ]
    }
  ]
}

When enabling analytics or embeds, extend the CSP entries accordingly.

10.2 Netlify headers (website/netlify.toml)

[[headers]]
for = "/*"
[headers.values]
Strict-Transport-Security = "max-age=31536000; includeSubDomains; preload"
X-Content-Type-Options     = "nosniff"
Referrer-Policy           = "strict-origin-when-cross-origin"
Permissions-Policy        = "camera=(), microphone=(), geolocation=(), fullscreen=(self)"
X-Frame-Options           = "DENY"
Content-Security-Policy   = """
default-src 'self';
base-uri 'self';
object-src 'none';
frame-ancestors 'self';
img-src 'self' data: blob:;
font-src 'self' data:;
style-src 'self' 'unsafe-inline';
script-src 'self';
connect-src 'self' https://rpc.example.tld;
form-action 'self';
upgrade-insecure-requests;
"""


⸻

11) Deployment Checklist
	•	HSTS, CSP, and other headers active in production.
	•	No secrets in client bundle (grep -R for obvious tokens).
	•	Analytics gated behind consent; test opt-in/opt-out flows.
	•	Embeds work with sandbox and CSP.
	•	Lockfile committed; third-party script versions pinned.
	•	Lighthouse security & best-practices pass.

Stay safe ✨
