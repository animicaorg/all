import type { MetadataRoute } from "next";

const SITE_URL = "https://pool.animica.org";

// Public, indexable routes. Account/dashboard/login are intentionally omitted.
const ROUTES: { path: string; priority: number }[] = [
  { path: "/", priority: 1.0 },
  { path: "/mine", priority: 0.9 },
  { path: "/mine/dual", priority: 0.9 },
  { path: "/mine/anm", priority: 0.8 },
  { path: "/mine/xmr", priority: 0.8 },
  { path: "/ai", priority: 0.7 },
  { path: "/rent", priority: 0.7 },
  { path: "/rent/cpu", priority: 0.6 },
  { path: "/rent/gpu", priority: 0.6 },
  { path: "/workers", priority: 0.6 },
  { path: "/workers/install", priority: 0.5 },
  { path: "/credits", priority: 0.6 },
  { path: "/stats", priority: 0.7 },
  { path: "/providers", priority: 0.5 },
  { path: "/revenue", priority: 0.5 },
  { path: "/payouts", priority: 0.5 },
  { path: "/docs", priority: 0.6 },
];

export default function sitemap(): MetadataRoute.Sitemap {
  return ROUTES.map(({ path, priority }) => ({
    url: `${SITE_URL}${path}`,
    changeFrequency: path === "/" || path.startsWith("/mine") ? "daily" : "weekly",
    priority,
  }));
}
