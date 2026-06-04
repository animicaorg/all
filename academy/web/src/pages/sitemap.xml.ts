import type { APIRoute } from "astro";
import { getCollection } from "astro:content";

const SITE = "https://academy.animica.org";

// Static, no-dependency sitemap: enumerates the fixed pages plus every
// tutorial in the content collection so new tutorials appear automatically
// on the next build. (Avoids pulling in @astrojs/sitemap.)
export const GET: APIRoute = async () => {
  const tutorials = await getCollection("tutorials");
  const staticPaths = ["/", "/tutorials", "/achievements", "/rewards"];
  const tutorialPaths = tutorials.map((t) => `/tutorials/${t.slug}`);
  const urls = [...staticPaths, ...tutorialPaths];

  const body =
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    urls
      .map(
        (p) =>
          `  <url><loc>${SITE}${p}</loc><changefreq>${
            p === "/" || p === "/tutorials" ? "weekly" : "monthly"
          }</changefreq></url>`,
      )
      .join("\n") +
    `\n</urlset>\n`;

  return new Response(body, {
    headers: { "Content-Type": "application/xml; charset=utf-8" },
  });
};
