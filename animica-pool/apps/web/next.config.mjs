/** @type {import('next').NextConfig} */
const API = process.env.API_BASE_URL || "http://localhost:4000";

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Proxy API + OpenAI-compatible routes to the NestJS backend so the browser
  // talks same-origin (session cookie works) and SDKs can use /v1 directly.
  // /serve runs the in-browser inference worker. COOP+COEP make the page
  // crossOriginIsolated, which unlocks SharedArrayBuffer → MULTI-THREADED wasm
  // inference (wllama). Without threads, prefilling a real ~10KB chat prompt on
  // one core takes minutes and the phone loses every race. All /serve assets are
  // fetched with CORS (jsDelivr + HF both send the needed headers), so
  // require-corp is safe here; scoped to /serve only.
  async headers() {
    return [
      {
        source: "/serve",
        headers: [
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
          { key: "Cross-Origin-Embedder-Policy", value: "require-corp" },
        ],
      },
    ];
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/v1/:path*", destination: `${API}/v1/:path*` },
    ];
  },
  // The desktop GUI-miner download was retired: pool.animica.org requires the
  // 10.3.1+ CLI client (animica up), which the GUI build did not report. Send
  // /download to the CLI get-started page so old links don't dead-end.
  async redirects() {
    return [
      { source: "/download", destination: "/mine", permanent: true },
      { source: "/download/:path*", destination: "/mine", permanent: true },
    ];
  },
};

export default nextConfig;
