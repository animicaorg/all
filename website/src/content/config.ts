import { defineCollection, z, type CollectionEntry } from "astro:content";

/**
 * Content Collections
 *
 * This file declares frontmatter schemas for Astro Content Collections.
 * Place markdown/mdx files under:
 *   - src/content/blog/   → uses the `blog` collection
 *   - src/content/docs/   → uses the `docs` collection
 *
 * You can query entries via `getCollection('blog')` or `getCollection('docs')`.
 */

// Blog posts: engineering updates, releases, deep dives.
const blog = defineCollection({
  type: "content",
  schema: ({ image }) =>
    z.object({
      title: z.string().min(1, "Post title is required"),
      description: z.string().max(280).optional(),
      date: z.coerce.date(), // accepts ISO string in frontmatter
      updated: z.coerce.date().optional(),
      author: z.string().default("Animica Team"),
      tags: z.array(z.string()).default([]),
      draft: z.boolean().default(false),
      hero: image().optional(), // e.g. /public/blog/hero.png
      canonicalUrl: z.string().url().optional(),
      // Optional search/SEO helpers
      ogTitle: z.string().optional(),
      ogDescription: z.string().optional(),
    }),
});

// Docs pages: optional in-repo docs rendered by the site (not the main docs site).
const docs = defineCollection({
  type: "content",
  schema: z.object({
    title: z.string().min(1, "Page title is required"),
    description: z.string().optional(),
    // Sidebar & navigation metadata
    group: z.string().optional(), // e.g., "Getting Started"
    order: z.number().int().optional(), // sort within group
    draft: z.boolean().default(false),
    // Optional redirect for stubs that live on the canonical docs site
    redirect: z.string().url().optional(),
  }),
});

export const collections = { blog, docs };

// Handy exported types
export type BlogEntry = CollectionEntry<"blog">;
export type DocsEntry = CollectionEntry<"docs">;
