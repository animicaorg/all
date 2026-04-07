# Animica — Website

Production marketing & documentation hub for Animica blockchain. Built with **Astro + TypeScript + Tailwind CSS** for a fast, mobile-first, accessible experience.

## 🌟 Features

- **Modern Design System**: Professional UI components (Button, Card, Badge, Accordion, etc.)
- **Mobile-First**: Responsive layouts optimized for all screen sizes
- **Dark Mode**: System preference detection with manual toggle
- **Fast Performance**: Static site generation with minimal JavaScript
- **Accessible**: WCAG compliant with semantic HTML and keyboard navigation
- **Data-Driven**: Content managed through data files for easy updates
- **SEO Optimized**: Meta tags, OpenGraph, Twitter cards, and sitemap

## 🚀 Quick Start

### Prerequisites

- Node.js 20+ and pnpm 9.0.0
- Git

### Development Setup

```bash
# Navigate to website directory
cd website

# Install dependencies
pnpm install

# Start development server
pnpm dev

# Open browser to http://localhost:4321
```

### Build for Production

```bash
# Build static site
pnpm build

# Preview production build
pnpm preview
```

## 📁 Project Structure

```
website/
├── src/
│   ├── components/      # Reusable UI components
│   │   ├── Button.astro
│   │   ├── Card.astro
│   │   ├── Badge.astro
│   │   ├── Accordion.astro
│   │   ├── Callout.astro
│   │   ├── DarkModeToggle.astro
│   │   ├── Header.astro
│   │   └── Footer.astro
│   ├── data/           # Content data files
│   │   ├── faq.ts      # FAQ content
│   │   └── roadmap.ts  # Roadmap milestones
│   ├── content/        # Markdown/MDX content
│   │   ├── blog/       # Blog posts
│   │   └── updates/    # Release updates
│   ├── layouts/        # Page layouts
│   │   └── BaseLayout.astro
│   ├── pages/          # Site pages (routes)
│   │   ├── index.astro       # Homepage
│   │   ├── about.astro       # About page
│   │   ├── technology.astro  # Technology deep dive
│   │   ├── faq.astro         # FAQ page
│   │   ├── roadmap.astro     # Roadmap page
│   │   └── ...
│   ├── config/         # Site configuration
│   │   ├── site.ts     # Global settings
│   │   └── links.ts    # External links
│   └── styles/         # Global styles
│       ├── global.css
│       ├── tokens.css
│       └── theme.css
├── public/             # Static assets
│   ├── icons/          # Logo and icons
│   ├── images/         # Images
│   ├── og/             # OpenGraph images
│   └── fonts/          # Web fonts
├── tailwind.config.mjs # Tailwind configuration
├── astro.config.mjs    # Astro configuration
├── CONTENT_GUIDE.md    # Content editing guide
└── README.md           # This file
```

## 📝 Editing Content

**For non-technical content updates, see [CONTENT_GUIDE.md](./CONTENT_GUIDE.md)**

Common tasks:

### Update FAQ

Edit `src/data/faq.ts` to add/edit questions:

```typescript
{
  category: "General",
  items: [
    {
      question: "What is Animica?",
      answer: "Animica is a post-quantum blockchain..."
    }
  ]
}
```

### Update Roadmap

Edit `src/data/roadmap.ts` to change milestone statuses:

```typescript
{
  title: "Feature Name",
  status: "done" | "in-progress" | "planned",
  category: "infrastructure",
  description: "Feature description"
}
```

### Add Blog Post

Create `src/content/blog/post-name.mdx`:

```mdx
---
title: "Post Title"
description: "Brief summary"
date: 2025-01-15
author: "Author Name"
tags: ["tag1", "tag2"]
---

Post content here...
```

## 🎨 Component Library

### Button

```astro
<Button variant="primary" size="lg" href="/docs">
  Get Started
</Button>
```

Variants: `primary`, `secondary`, `outline`, `ghost`, `danger`  
Sizes: `sm`, `md`, `lg`

### Card

```astro
<Card variant="elevated" padding="lg" hoverable>
  <h3>Card Title</h3>
  <p>Card content</p>
</Card>
```

Variants: `default`, `bordered`, `elevated`, `flat`  
Padding: `none`, `sm`, `md`, `lg`

### Badge

```astro
<Badge variant="success" dot>Live</Badge>
```

Variants: `default`, `success`, `warning`, `error`, `info`, `planned`

### Accordion

```astro
<Accordion title="Question here?">
  Answer content here
</Accordion>
```

### Callout

```astro
<Callout type="warning" title="Important">
  Warning message here
</Callout>
```

Types: `info`, `success`, `warning`, `error`, `note`

## ⚙️ Configuration

### Environment Variables

Create `.env` file (copy from `.env.example`):

```bash
# Site URL (for sitemap and canonical URLs)
SITE_URL=https://animica.org

# External service URLs
ANIMICA_RPC_URL=http://127.0.0.1:8545
ANIMICA_EXPLORER_URL=https://explorer.animica.org
ANIMICA_GITHUB_URL=https://github.com/animicaorg/all

# Optional services
ANIMICA_FAUCET_URL=
ANIMICA_POOL_URL=
ANIMICA_MINING_API_BASE_URL=
ANIMICA_DISCORD_URL=
ANIMICA_TELEGRAM_URL=
ANIMICA_X_URL=https://x.com/animica

# Chain configuration
ANIMICA_CHAIN_ID=1
```

### Site Configuration

Edit `src/config/site.ts` for:
- Site title and tagline
- Navigation menu
- Footer links
- Social media URLs
- Contact information
- Mining API base URL override for split website/pool deployments

## 🧪 Testing

```bash
# Type checking
pnpm typecheck

# Linting
pnpm lint

# Unit tests
pnpm test

# Build test
pnpm build
```

## 📦 Deployment

### Docker

```bash
# Build and run with Docker Compose
docker compose -f docker-compose.website.yml up --build

# Access at http://localhost:4321
```

### Static Hosting

The built site (`dist/` folder) can be deployed to:
- **Vercel**: Auto-deploy from GitHub (see `vercel.json`)
- **Netlify**: Auto-deploy from GitHub (see `netlify.toml`)
- **Cloudflare Pages**: Connect GitHub repository
- **Any static host**: Upload `dist/` folder

### Build Process

```bash
# Production build
pnpm build

# Output directory
dist/

# Verify build
pnpm preview
```

## 🔧 Customization

### Design Tokens

Edit `tailwind.config.mjs` for:
- Brand colors
- Typography scale
- Spacing scale
- Border radius
- Shadows and effects

### Theme Variables

Edit `src/styles/tokens.css` for CSS custom properties used across the site.

## 📱 Mobile-First Design

All pages are optimized for mobile:
- Touch-friendly interactions (min 44x44px tap targets)
- Responsive typography
- Mobile navigation menu
- Optimized images and fonts
- No horizontal scroll

Test on:
- iPhone (375px width)
- iPad (768px width)
- Desktop (1280px+ width)

## ♿ Accessibility

- Semantic HTML5 elements
- ARIA labels where needed
- Keyboard navigation support
- Focus visible states
- Skip links for main content
- Color contrast compliance
- Screen reader tested

## 🚀 Performance

- Static site generation (SSG)
- Minimal JavaScript
- Optimized images (WebP, lazy loading)
- Font preloading
- Code splitting
- CSS purging

Target Lighthouse scores:
- Performance: 90+
- Accessibility: 95+
- Best Practices: 95+
- SEO: 100

## 🆘 Troubleshooting

### Build Fails

1. Clear cache: `rm -rf .astro dist node_modules/.astro`
2. Reinstall: `pnpm install`
3. Rebuild: `pnpm build`

### Port Already in Use

```bash
# Use different port
pnpm dev -- --port 4322
```

### Images Not Loading

- Verify images are in `public/` folder
- Check paths start with `/` (e.g., `/images/photo.png`)
- Filenames are case-sensitive

## 📚 Resources

- **Astro Docs**: https://docs.astro.build
- **Tailwind CSS**: https://tailwindcss.com/docs
- **Content Guide**: [CONTENT_GUIDE.md](./CONTENT_GUIDE.md)
- **Main Repo**: https://github.com/animicaorg/all

## 🤝 Contributing

1. Create a feature branch
2. Make your changes
3. Test thoroughly (`pnpm build`, `pnpm test`)
4. Update CONTENT_GUIDE.md if adding new content types
5. Submit a pull request

## 📄 License

See [LICENSE.txt](../LICENSE.txt) in the repository root.
