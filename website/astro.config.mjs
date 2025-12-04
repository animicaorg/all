import 'dotenv/config';
import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';
import sitemap from '@astrojs/sitemap';
import tailwind from '@astrojs/tailwind';
// Optional: generates robots.txt from policy.
// If you haven't added it yet, run: pnpm add -D @astrojs/robots

// Resolve site URL (required for sitemap & absolute OG tags)
const SITE_URL = process.env.SITE_URL?.replace(/\/+$/, '') || 'https://animica.org';

// Allow-listed remote image hosts (extend as needed)
const IMAGE_DOMAINS = [
  'assets.animica.org',
  'images.animica.org',
];

export default defineConfig({
  site: SITE_URL,
  // Enable HTML compression in production
  compressHTML: true,

  integrations: [
    mdx(),
    tailwind({
      // PostCSS/Tailwind are auto-detected; config lives in tailwind.config.js if present
      applyBaseStyles: true,
    }),
    sitemap({
      filter: (page) => !page.includes('/drafts/'), // skip drafts if any
      i18n: false,
    }),
  ],

  // Built-in image optimizer (Astro Assets).
  // Uses Sharp when available; falls back to Squoosh in the browser build step.
  image: {
    service: {
      // Prefer native Sharp in Node for best performance (install 'sharp' automatically via astro)
      entrypoint: 'astro/assets/services/sharp',
      config: {
        // You can tune default formats/quality here if desired
        // formats: ['avif', 'webp', 'png'],
        // quality: 80,
      },
    },
    domains: IMAGE_DOMAINS,
    // Generate <img> sizes automatically for responsive images (opt-in)
    // experimentalAutoTitle: false
  },

  vite: {
    // Useful aliases for cleaner imports (optional)
    resolve: {
      alias: {
        '@': new URL('./src', import.meta.url).pathname,
        '@content': new URL('./src/content', import.meta.url).pathname,
        '@components': new URL('./src/components', import.meta.url).pathname,
      },
    },
    build: {
      // Smaller JS by default; adjust if you need legacy support
      target: 'es2020',
    },
  },
});
