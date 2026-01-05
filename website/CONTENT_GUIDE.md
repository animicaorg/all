# Content Management Guide

This guide explains how to update content on the Animica website without touching code.

## Overview

The website uses a data-driven approach where most content lives in easily editable data files. This allows non-technical team members to update content without modifying components or pages.

## Content Locations

### 1. FAQ Content (`src/data/faq.ts`)

**Purpose**: Frequently asked questions organized by category

**Structure**:
```typescript
{
  category: "Category Name",
  items: [
    {
      question: "Question text?",
      answer: "Answer text with full explanation."
    }
  ]
}
```

**How to edit**:
1. Open `src/data/faq.ts`
2. Find the category you want to edit
3. Add, remove, or modify questions and answers
4. Save the file
5. The FAQ page (`/faq`) will automatically reflect your changes

**Example**: Adding a new question
```typescript
{
  question: "How do I backup my wallet?",
  answer: "To backup your wallet, export your mnemonic phrase from the wallet settings. Store it securely offline in multiple locations."
}
```

### 2. Roadmap Content (`src/data/roadmap.ts`)

**Purpose**: Development milestones organized by phase

**Structure**:
```typescript
{
  phase: "Phase Name (Q1 2025)",
  description: "Brief phase description",
  items: [
    {
      title: "Feature Name",
      description: "Detailed description",
      status: "done" | "in-progress" | "planned",
      date: "Q1 2025", // optional
      category: "infrastructure" | "consensus" | "execution" | "tooling" | "ecosystem"
    }
  ]
}
```

**How to edit**:
1. Open `src/data/roadmap.ts`
2. Find the phase you want to edit
3. Update item statuses as features are completed
4. Add new items or phases as needed
5. Save the file
6. The Roadmap page (`/roadmap`) will automatically update

**Status values**:
- `done`: Completed features (green badge)
- `in-progress`: Currently being developed (yellow badge with dot)
- `planned`: Future work (gray badge)

**Category icons**:
- `infrastructure`: 🏗️ (Core blockchain, networking, databases)
- `consensus`: ⚙️ (PoIES, validation, difficulty adjustment)
- `execution`: 🐍 (Python-VM, contracts, gas)
- `tooling`: 🛠️ (SDKs, CLI, development tools)
- `ecosystem`: 🌐 (Wallets, explorer, community apps)

### 3. Blog Posts (`src/content/blog/`)

**Purpose**: Announcements, technical deep dives, and updates

**How to add a blog post**:
1. Create a new `.mdx` file in `src/content/blog/`
2. Add frontmatter with metadata:
```mdx
---
title: "Post Title"
description: "Brief summary (max 280 characters)"
date: 2025-01-15
author: "Author Name"
tags:
  - tag1
  - tag2
hero: "/og/og-home.png"  # optional hero image
draft: false  # set to true to hide
---

Your content here in Markdown/MDX format...
```

### 4. Updates (`src/content/updates/`)

**Purpose**: Short release notes and status updates

**How to add an update**:
1. Create a new `.mdx` file in `src/content/updates/`
2. Name it with date prefix: `YYYY-MM-DD-slug.mdx`
3. Add frontmatter:
```mdx
---
title: "Update Title"
description: "Brief summary"
date: 2025-01-15
tags: ["release", "feature"]
---

Brief update content here...
```

### 5. Site Configuration (`src/config/site.ts`)

**Purpose**: Global site settings, navigation, social links

**What you can edit**:
- Site title and tagline
- Navigation menu items
- Footer links
- Social media URLs
- Contact information

**How to edit**:
1. Open `src/config/site.ts`
2. Modify the `SITE` object
3. Common changes:
   - Update social media links in `social` object
   - Add/remove navigation items in `nav.top` array
   - Update footer sections in `nav.footer` array

## Homepage Sections

To edit homepage content, open `src/pages/index.astro` and modify:

### Hero Section
```typescript
const heroTitle = "Animica";
const heroSubtitle = "Post-quantum blockchain...";
const heroDesc = "A production-ready L1 with...";
```

### Proof Points
Edit the `proofPoints` array to change the 4 key feature cards.

### Get Started Cards
Edit the `getStartedCards` array to change the 4 action cards.

### Stats Bar
Edit the `stats` array to update the metrics shown below the hero.

## Page Metadata (SEO)

Each page has metadata for SEO. To edit:

1. Open the page file (e.g., `src/pages/about.astro`)
2. Find the variables at the top:
```typescript
const pageTitle = "About Animica";
const pageDesc = "Learn about Animica's mission...";
```
3. Update the text
4. This affects:
   - Browser tab title
   - Search engine results
   - Social media previews

## Images and Assets

### Adding Images

1. Place images in `public/images/`
2. Reference them in content with `/images/filename.png`
3. Use descriptive filenames (e.g., `architecture-diagram.png`)

### Image Guidelines

- **Format**: Use WebP for photos, PNG for diagrams/logos, SVG for icons
- **Size**: Optimize images before uploading (max 500KB for photos)
- **Alt text**: Always provide descriptive alt text for accessibility

### OG Images (Social Previews)

Social media preview images go in `public/og/`:
- `og-home.png`: Homepage preview (1200x630px)
- `og-default.png`: Default fallback

## Building and Testing

After making changes:

```bash
# Install dependencies (first time only)
pnpm install

# Start development server
pnpm dev

# Build for production
pnpm build

# Preview production build
pnpm preview
```

Visit `http://localhost:4321` to see your changes.

## Best Practices

### Writing Content

1. **Be concise**: Users scan, not read. Get to the point quickly.
2. **Use headings**: Break up long content with clear section headings.
3. **Active voice**: "Run a node" not "A node can be run"
4. **Avoid jargon**: Explain technical terms or link to documentation.
5. **Call to action**: Every page should guide users to the next step.

### FAQ Guidelines

- **Question format**: Use natural language ("How do I...?" not "Method for...")
- **Answer length**: 2-4 sentences ideal, max 1 paragraph
- **Link to docs**: For detailed technical info, link to relevant documentation
- **Categories**: Keep questions in logical categories

### Roadmap Guidelines

- **Be realistic**: Only mark items as "done" when fully completed
- **Update regularly**: Review monthly and update statuses
- **Clear descriptions**: Explain what the feature does, not how it works
- **Dates**: Only add dates for near-term milestones (current quarter)

### Blog Post Guidelines

- **Title**: Clear, descriptive, under 60 characters
- **Description**: Compelling summary, 120-280 characters
- **Length**: Aim for 500-1500 words
- **Structure**: Intro → Main content → Conclusion with CTA
- **Code blocks**: Use syntax highlighting (triple backticks with language)

## Troubleshooting

### Build Fails After Edit

1. Check for syntax errors in data files (missing commas, quotes)
2. Run `pnpm build` and read the error message
3. If you see "frontmatter does not match", check your blog/update metadata

### Content Not Updating

1. Stop the dev server (Ctrl+C)
2. Delete `.astro` folder if it exists
3. Run `pnpm dev` again

### Images Not Showing

1. Verify image is in `public/` folder
2. Check path starts with `/` (e.g., `/images/photo.png`)
3. Check filename matches exactly (case-sensitive)

## Need Help?

- **Documentation**: Check `/docs` for technical details
- **GitHub**: Open an issue for bugs or suggestions
- **Community**: Ask in Discord for content questions

## Quick Reference

| Content Type | File Location | Page URL |
|-------------|---------------|----------|
| FAQ | `src/data/faq.ts` | `/faq` |
| Roadmap | `src/data/roadmap.ts` | `/roadmap` |
| Blog | `src/content/blog/*.mdx` | `/blog/[slug]` |
| Updates | `src/content/updates/*.mdx` | `/updates/[slug]` |
| Homepage | `src/pages/index.astro` | `/` |
| About | `src/pages/about.astro` | `/about` |
| Technology | `src/pages/technology.astro` | `/technology` |
