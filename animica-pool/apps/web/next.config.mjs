/** @type {import('next').NextConfig} */
const API = process.env.API_BASE_URL || "http://localhost:4000";

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Proxy API + OpenAI-compatible routes to the NestJS backend so the browser
  // talks same-origin (session cookie works) and SDKs can use /v1 directly.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/v1/:path*", destination: `${API}/v1/:path*` },
    ];
  },
};

export default nextConfig;
