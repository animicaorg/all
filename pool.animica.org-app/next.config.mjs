// The OpenAI-compatible API lives at the domain ROOT (/v1/...) so SDKs work by
// only changing baseURL. The app is served under basePath "/app", so the route
// files live at src/app/v1/... and are served at /app/v1/...
//
// Next 14 refuses to internally rewrite a root path (/v1) across the basePath,
// so the root→/app mapping is done by the reverse proxy that already fronts
// this app. In production, nginx maps it directly:
//
//   location /v1/ { proxy_pass http://127.0.0.1:3020/app/v1/; }
//
// Locally (no nginx) the API is reachable at http://localhost:3020/app/v1/...
// — set the OpenAI client baseURL to that, or run nginx/Caddy with the rule
// above to get the clean /v1 root.

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Served behind nginx at pool.animica.org/app — every route and asset
  // lives under /app.
  basePath: "/app",
  assetPrefix: "/app",
  reactStrictMode: true,
  poweredByHeader: false,
  // Production (`next start`) builds/serves from a separate dir so a
  // concurrent `next dev` in this same directory can't clobber the prod
  // build's .next. Dev uses the default ".next"; prod sets NEXT_DIST_DIR.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  experimental: {
    serverActions: { allowedOrigins: ["pool.animica.org", "localhost:3020"] },
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
