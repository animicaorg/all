# Explorer Web — Deployment Guide (Static Hosting & Caching)

This app is a client-only SPA (React + Vite). It serves **static assets** and talks to external **RPC/WS** and optional **Studio Services** APIs. This guide focuses on production-ready static hosting and cache headers.

---

## 1) Build Artifacts

```bash
# from explorer-web/
npm ci
npm run build
# → dist/
#   index.html
#   assets/*.js, *.css, *.wasm, *.map (content-hashed file names)
#   icons/, manifest.webmanifest, robots.txt

	•	Content-hashed assets (assets/*.hash.js|css|wasm) are immutable and safe for long-lived caching.
	•	HTML (index.html) must be non-cached to allow config/rollouts.
	•	Source maps: keep server-side, gated, or off in prod (build.sourcemap=false) if you don’t need them.

⸻

2) SPA Routing

For client-side routes, configure a single-page fallback:
	•	S3/CloudFront: Custom Error Response mapping 404 → /index.html (200).
	•	NGINX: try_files $uri /index.html;
	•	Vercel/Netlify: use a rewrite rule to /index.html.

⸻

3) Cache-Control Strategy

HTML
	•	Cache-Control: no-store, must-revalidate
	•	ETag: optional (but no-store makes it irrelevant)
	•	Rationale: fetch fresh HTML to pick up new builds and runtime config.

Static assets (content-hashed js/css/wasm/fonts/img)
	•	Cache-Control: public, max-age=31536000, immutable
	•	ETag: optional (immutable makes it redundant)
	•	Rationale: safe to cache forever; URL changes on rebuild.

Web App Manifest / Icons
	•	Cache-Control: public, max-age=3600 (or shorter if you iterate often)

Source maps (if served)
	•	Cache-Control: private, max-age=0, must-revalidate

⸻

4) Security & Transport Headers (Recommended)

Serve over HTTPS only.

Security headers

Content-Security-Policy: default-src 'none';
  connect-src 'self' https://RPC_HOST wss://RPC_HOST https://SERVICES_HOST;
  script-src 'self';
  style-src 'self';
  img-src 'self' data:;
  font-src 'self';
  frame-ancestors 'none';
  base-uri 'none';
  form-action 'none';
  upgrade-insecure-requests;
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
Permissions-Policy: geolocation=(), microphone=(), camera=(), usb=()

If you terminate at a CDN, set these at the CDN and/or origin.
Match CSP connect-src to your RPC/WS/Services endpoints.

⸻

5) CORS

Static hosting normally doesn’t need CORS. Your RPC/WS/Services servers must allow your app origin:
	•	Access-Control-Allow-Origin: https://app.example.com
	•	Avoid * when credentials are involved (we don’t send any by default).
	•	Preflight caching: Access-Control-Max-Age: 600 (sensible default).

⸻

6) Compression
	•	Enable Brotli (preferred) and Gzip at CDN and/or origin.
	•	Precompress if supported (Vercel/Netlify/CDNs auto-compress).
	•	WASM: ensure Content-Type: application/wasm.

⸻

7) Example Configs

NGINX (origin or edge)

server {
  listen 443 ssl http2;
  server_name app.example.com;

  root /var/www/explorer-web/dist;

  # HTML: no-store
  location = /index.html {
    add_header Cache-Control "no-store, must-revalidate";
    try_files $uri =404;
  }

  # SPA fallback
  location / {
    try_files $uri /index.html;
  }

  # Immutable assets
  location /assets/ {
    add_header Cache-Control "public, max-age=31536000, immutable";
    try_files $uri =404;
  }

  # Manifest & icons
  location ~* \.(webmanifest|png|svg|ico)$ {
    add_header Cache-Control "public, max-age=3600";
    try_files $uri =404;
  }

  # WASM & compression
  types { application/wasm wasm; }
  gzip on;
  gzip_types text/plain text/css application/javascript application/json application/wasm;

  # Security headers (trim for readability)
  add_header X-Content-Type-Options "nosniff" always;
  add_header Referrer-Policy "strict-origin-when-cross-origin" always;
  add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
  add_header Content-Security-Policy "default-src 'none'; connect-src 'self' https://RPC_HOST wss://RPC_HOST https://SERVICES_HOST; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; upgrade-insecure-requests" always;
}

S3 + CloudFront

S3 object metadata
	•	index.html: Cache-Control: no-store, must-revalidate
	•	assets/*: Cache-Control: public, max-age=31536000, immutable

CloudFront behaviors
	•	Default behavior → /index.html (TTL 0), Compress on, Viewer Protocol: Redirect to HTTPS.
	•	Path pattern /assets/* → TTL 1y, Compress on.
	•	Error pages: Map 404/403 to /index.html with 200 for SPA routing.
	•	Add Response Headers Policy with security headers above.

Vercel / Netlify
	•	Use project defaults; add a headers file or UI rules:
	•	/index.html: Cache-Control: no-store, must-revalidate
	•	/assets/*: Cache-Control: public, max-age=31536000, immutable
	•	Security headers via vercel.json / netlify.toml or dashboard.
	•	SPA routing: automatic with rewrites to /index.html.

⸻

8) Environment Configuration

The app reads endpoints via VITE_* at build time:
	•	VITE_RPC_URL, VITE_CHAIN_ID, VITE_EXPLORER_API (optional), VITE_SERVICES_URL (optional)
	•	Produce one build per environment (staging/prod), or serve a small /config.json (no-store) that the app fetches at boot (requires a tiny loader tweak).

Do not embed secrets in VITE_* values. The explorer is public and read-only.

⸻

9) WebSocket Pass-through
	•	If fronted by a CDN/proxy, enable WS pass-through on the path used by your node (e.g., /ws).
	•	Enforce wss:// only; set reasonable idle timeouts and keep-alives.

⸻

10) Rollout & Invalidation
	•	Blue/Green or Canary: Upload new assets first, then publish new index.html.
	•	CDN invalidation: Invalidate /index.html (and any non-hashed files) after deploy.
	•	Hashed assets don’t need invalidation.

⸻

11) Monitoring
	•	Track availability and TLS of the hosting site.
	•	Optionally monitor WS connection success rate (client-side metrics must be opt-in).

⸻

12) Checklist
	•	HTTPS/WSS enabled
	•	SPA fallback → /index.html
	•	index.html → Cache-Control: no-store, must-revalidate
	•	assets/* → public, max-age=31536000, immutable
	•	WASM served as application/wasm
	•	Brotli/Gzip on
	•	Security headers (CSP, HSTS, nosniff, etc.)
	•	WS pass-through configured
	•	CDN invalidation for HTML on each deploy

