/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // nginx server-level sub_filter injects the upgrade banner; it cannot rewrite compressed
  // upstreams, so we disable Next's compression and the proxy sets Accept-Encoding "".
  compress: false,
  poweredByHeader: false,
  eslint: { ignoreDuringBuilds: true },
  typescript: { ignoreBuildErrors: false },
  async headers() {
    return [
      {
        // Embed widget + embeddable listing frames may be framed anywhere.
        source: '/embed/:path*',
        headers: [{ key: 'Content-Security-Policy', value: 'frame-ancestors *' }],
      },
      {
        // Everything else: same-origin framing only.
        source: '/((?!embed).*)',
        headers: [{ key: 'X-Frame-Options', value: 'SAMEORIGIN' }],
      },
    ];
  },
};

export default nextConfig;
