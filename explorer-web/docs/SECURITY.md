# Explorer Web — Security Model

**Scope:** This document covers the security posture of the `explorer-web` application.  
It is a **read-only** explorer UI. It does **not** hold keys, perform signing, or submit privileged mutations on behalf of users.

---

## 1) Threat Model (at a glance)

- **Assets:** User privacy, UI integrity, correctness of on-chain data shown.
- **Trusted:** Public blockchain RPC endpoint(s), optional Studio Services (read-only paths).
- **Untrusted:** The open internet (all browsers), WS frames, REST responses, all user-supplied query params.
- **Non-Goals:** Key custody, server-side signing, privileged administration.

---

## 2) Principles

1. **Read-only by design.** No private keys and no signing logic ever run in this app.
2. **No secrets in the client.** No API secrets, bearer tokens, or admin keys are bundled or stored.
3. **Least privilege network access.** Only fetches from RPC/WS and optional services with CORS allowlists.
4. **Fail closed.** On CORS/transport errors, show explicit “stale/blocked” states; do not silently degrade into insecure paths.
5. **Deterministic rendering.** All untrusted data is parsed, validated, and rendered safely (no HTML injection).

---

## 3) Data Flow & Trust Boundaries

- **HTTP (RPC / Explorer API / Services):** `fetch` with timeouts and retries. Responses are validated (types/zod schemas).
- **WebSocket (newHeads, etc.):** `wss://` only. Frames are parsed in a worker and coalesced to reduce UI pressure.
- **Local State:** In-memory stores (Zustand).  
  - **LocalStorage/IndexedDB:** Used sparingly (non-sensitive UI prefs only). No secrets or PII.

**No PII collection.** The app does not request or store personally identifiable information. If you add optional telemetry, it must be **off by default** and clearly disclosed.

---

## 4) Authentication, Authorization, & Keys

- The explorer is **public read-only**.  
- If the optional Studio Services require API keys, those keys **must not** be embedded in the web app. Prefer:
  - Server-side reverse proxy that injects credentials, or
  - Per-user keys entered at runtime and stored only in memory.
- The app will refuse to proceed if a “secret-looking” value is provided via `VITE_*` envs at build time.

---

## 5) Rate Limits & DoS Hygiene

While the client cannot enforce server limits, it **cooperates** to reduce load:

- **Client backoff & jitter** for failing endpoints.
- **Debounced searches** and **coalesced WS events** (worker) to avoid thundering herds.
- **Paging & windows** for heavy lists/charts; no unbounded fetches.
- **Retry caps** and circuit-breaker states to stop hammering misbehaving endpoints.

Servers (RPC/Services) should enforce:
- IP/key-based token buckets.
- 429 responses with `Retry-After`.
- CORS allowlists per origin and path.

---

## 6) Browser Security Hardening

### Required server headers (recommendations)
- **CSP** (Content-Security-Policy) example:

default-src ‘none’;
connect-src ‘self’ https://rpc.example.com wss://rpc.example.com https://services.example.com;
script-src ‘self’;
style-src ‘self’;
img-src ‘self’ data:;
font-src ‘self’;
frame-ancestors ‘none’;
base-uri ‘none’;
form-action ‘none’;
upgrade-insecure-requests;

- **X-Content-Type-Options:** `nosniff`
- **Referrer-Policy:** `strict-origin-when-cross-origin`
- **Cross-Origin-Opener-Policy:** `same-origin`
- **Cross-Origin-Embedder-Policy:** `require-corp` (if using advanced features; otherwise consider `credentialless`)
- **Strict-Transport-Security:** `max-age=31536000; includeSubDomains; preload`
- **Permissions-Policy:** disable unneeded features (camera, mic, geolocation, etc.)

### Transport
- Only **HTTPS/WSS**. Mixed content is blocked.
- Validate that configured RPC/WS URLs are TLS endpoints.

---

## 7) XSS, Injection & Rendering Safety

- **Never** inject raw HTML from RPC/Services into the DOM.  
- Render text using React (escaping by default).  
- Any “rich” formatting must pass through a strict sanitizer with an allowlist (prefer **no HTML** in payloads).
- **JSON only** over RPC/WS; reject non-JSON or excessively large frames.
- All user inputs (filters, searches) are treated as **data**, never code.

---

## 8) State, Storage & Caching

- **No secrets** in `localStorage`, `sessionStorage`, `IndexedDB`, or URL params.
- Persist only harmless UI preferences (theme, collapsed panels).
- Avoid storing full RPC responses; cache shallow summaries when needed.
- Provide a “Reset data” control that clears persisted keys.

---

## 9) Dependencies & Supply Chain

- Enforce **exact versions** via lockfiles (npm `package-lock.json` or `pnpm-lock.yaml`).
- Run dependency audits (e.g., `npm audit`) in CI; fail on critical vulns.
- Use **Subresource Integrity (SRI)** for any CDN assets (if ever used).
- Prefer **first-party builds** of WASM/worker bundles; avoid remote code loading.
- Review transitive dependencies that can execute code at build time (postinstall scripts disabled in CI if possible).

---

## 10) Error Handling & Observability

- Errors surface as user-friendly toasts; internal details in console (dev only).
- No stack traces or PII sent over the network.
- Optional metrics may include **anonymous** counters (e.g., WS reconnects) but must be opt-in.

---

## 11) CORS & Origin Policy

- RPC/Services should expose **strict CORS**:
- Allowed origins: specific app domains only.
- Allowed methods: `GET`, `POST` (read-only endpoints).
- Allowed headers: minimal set (`content-type`).
- **No** wildcard `*` with credentials.
- Preflight responses should be short-lived and cacheable.

---

## 12) WebSocket Robustness

- Only connect to configured **wss://** endpoints.
- Auto-reconnect with exponential backoff and jitter.
- Size limits on frames; drop or ignore oversize messages.
- Coalesce frames in a Worker (prevents UI thread flooding).
- Detect and handle reorg signals; re-query canonical slices via HTTP.

---

## 13) Internationalization & Content Safety

- Locale strings loaded from static JSON under our control.
- No remote execution via localized content.
- Number/date formatting uses safe libraries (no `eval`/Function).

---

## 14) Building & Deployment

- Production builds are immutable and content-addressed.
- Serve with strong caching headers for static assets; **no** caching for HTML.
- Continuous Delivery signs artifacts or stores digests for provenance (optional but recommended).

---

## 15) Responsible Disclosure

If you discover a vulnerability:
- Please report privately to the maintainers (see repository SECURITY.md or contact).  
- We’ll acknowledge and remediate quickly; coordinated disclosure is appreciated.

---

## 16) Checklist (TL;DR)

- [x] Read-only functionality; no signing; no secrets
- [x] HTTPS/WSS only; strict CORS
- [x] CSP with `default-src 'none'`, explicit allowlists
- [x] No HTML injection; sanitize or avoid entirely
- [x] Client backoff; coalesced WS frames; paginated queries
- [x] No PII; minimal local persistence; reset option
- [x] Pinned deps; CI audit; SRI for any CDN assets
- [x] Clear error states; no leaking sensitive traces
- [x] Opt-in telemetry only (if any)

This document should be reviewed whenever endpoints, dependencies, or chart/worker pipelines change.
