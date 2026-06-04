import type { MetadataRoute } from "next";

const SITE_URL = "https://pool.animica.org";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        // Account/dashboard areas hold no public SEO value.
        disallow: ["/dashboard", "/login", "/api/"],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}
