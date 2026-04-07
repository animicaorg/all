import 'dotenv/config';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';
import sitemap from '@astrojs/sitemap';
import tailwind from '@astrojs/tailwind';
import { createMalformedUriMiddleware } from './scripts/malformed-uri-middleware.mjs';
// Optional: generates robots.txt from policy.
// If you haven't added it yet, run: pnpm add -D @astrojs/robots

// Resolve site URL (required for sitemap & absolute OG tags)
const SITE_URL = process.env.SITE_URL?.replace(/\/+$/, '') || 'https://animica.org';

// Allow-listed remote image hosts (extend as needed)
const IMAGE_DOMAINS = [
  'assets.animica.org',
  'images.animica.org',
];

const projectRoot = fileURLToPath(new URL('.', import.meta.url));

const aliases = {
  '@': resolve(projectRoot, 'src'),
  '@content': resolve(projectRoot, 'src/content'),
  '@components': resolve(projectRoot, 'src/components'),
};

const sanitizeMalformedUriPlugin = {
  name: 'sanitize-malformed-uri',
  enforce: 'post',
  configureServer(server) {
    return () => {
      server.middlewares.stack.unshift({
        route: '',
        handle: createMalformedUriMiddleware(),
      });
    };
  },
};

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
    plugins: [sanitizeMalformedUriPlugin],
    // Useful aliases for cleaner imports (optional)
    resolve: {
      alias: aliases,
    },
    build: {
      // Smaller JS by default; adjust if you need legacy support
      target: 'es2020',
    },
    server: {
      // Allow access from animica.org and common subdomains
      allowedHosts: [
        'animica.org',
        'www.animica.org',
        'dev.animica.org',
        'staging.animica.org',
        'preview.animica.org',
        'explorer.animica.org',
      ],
    },
  },
});
