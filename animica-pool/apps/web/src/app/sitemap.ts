import type { MetadataRoute } from "next";

const SITE_URL = "https://pool.animica.org";

// Public, indexable routes. Account/dashboard/login are intentionally omitted.
const ROUTES: { path: string; priority: number }[] = [
  { path: "/", priority: 1.0 },
  { path: "/mine", priority: 0.9 },
  { path: "/serve", priority: 0.9 },
  { path: "/ai", priority: 0.7 },
  { path: "/training-pools", priority: 0.7 },
  { path: "/about-ena", priority: 0.6 },
  { path: "/bittensor", priority: 0.6 },
  { path: "/workers", priority: 0.6 },
  { path: "/workers/install", priority: 0.5 },
  { path: "/credits", priority: 0.6 },
  { path: "/stats", priority: 0.7 },
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
