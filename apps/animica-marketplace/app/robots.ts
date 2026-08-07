import type { MetadataRoute } from 'next';

// /robots.txt — the public catalog (apps, functions, developers, docs, pricing) is fully
// crawlable; account consoles and the API surface are not pages and are excluded.

const BASE = process.env.PUBLIC_BASE_URL || 'https://animica.dev';

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        // NOTE: '/dev/' must keep its trailing slash — a bare '/dev' prefix would also
        // block /developers. '/cloud' (no slash) covers the console root and all subpages.
        disallow: ['/api/', '/admin/', '/dev/', '/cloud', '/settings/', '/dashboard/'],
      },
    ],
    sitemap: `${BASE}/sitemap.xml`,
  };
}
