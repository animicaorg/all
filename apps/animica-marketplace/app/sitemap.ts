import type { MetadataRoute } from 'next';
import { prisma } from '../lib/db';

// /sitemap.xml — enumerates the REAL public catalog from the database on every request:
// every PUBLISHED + PUBLIC CloudApp page and every claimed developer handle, plus the static
// public routes. Served dynamically so a newly published app is crawlable immediately.

export const dynamic = 'force-dynamic';

const BASE = process.env.PUBLIC_BASE_URL || 'https://animica.dev';

const STATIC_ROUTES: Array<{ path: string; priority: number; changeFrequency: MetadataRoute.Sitemap[number]['changeFrequency'] }> = [
  { path: '/', priority: 1, changeFrequency: 'daily' },
  { path: '/apps', priority: 0.9, changeFrequency: 'hourly' },
  // Served by nginx from the x402 gateway, not by this app, so it is invisible
  // to route discovery and has to be listed explicitly.
  { path: '/x402', priority: 0.9, changeFrequency: 'daily' },
  { path: '/functions', priority: 0.8, changeFrequency: 'hourly' },
  { path: '/developers', priority: 0.7, changeFrequency: 'daily' },
  { path: '/pricing', priority: 0.7, changeFrequency: 'weekly' },
  { path: '/docs', priority: 0.7, changeFrequency: 'weekly' },
  { path: '/names', priority: 0.5, changeFrequency: 'weekly' },
  { path: '/portal', priority: 0.5, changeFrequency: 'weekly' },
  { path: '/vpn', priority: 0.4, changeFrequency: 'weekly' },
  { path: '/hire', priority: 0.4, changeFrequency: 'weekly' },
];

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const out: MetadataRoute.Sitemap = STATIC_ROUTES.map((r) => ({
    url: `${BASE}${r.path}`,
    changeFrequency: r.changeFrequency,
    priority: r.priority,
  }));

  // Every published, public app page. Unlisted/private/suspended apps never appear.
  try {
    const apps = await prisma.cloudApp.findMany({
      where: { status: 'PUBLISHED', visibility: 'PUBLIC', suspendedAt: null },
      select: { slug: true, updatedAt: true },
      orderBy: { publishedAt: 'desc' },
      take: 5000,
    });
    for (const a of apps) {
      out.push({
        url: `${BASE}/apps/${encodeURIComponent(a.slug)}`,
        lastModified: a.updatedAt,
        changeFrequency: 'daily',
        priority: 0.8,
      });
    }
  } catch {
    // DB unavailable: still serve the static routes rather than a 500.
  }

  // Every claimed developer handle.
  try {
    const devs = await prisma.account.findMany({
      where: { handle: { not: null } },
      select: { handle: true, updatedAt: true },
      take: 5000,
    });
    for (const d of devs) {
      if (!d.handle) continue;
      out.push({
        url: `${BASE}/developers/${encodeURIComponent(d.handle)}`,
        lastModified: d.updatedAt,
        changeFrequency: 'weekly',
        priority: 0.6,
      });
    }
  } catch {
    // DB unavailable: static routes only.
  }

  return out;
}
