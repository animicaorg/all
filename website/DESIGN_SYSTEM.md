# Animica Website Design System

This document describes the modern design system implemented for the Animica website.

## Design Principles

1. **Modern & Premium**: Clean, sophisticated aesthetic with subtle animations
2. **Performance-First**: Optimized assets, lazy loading, efficient animations
3. **Accessible**: WCAG 2.1 AA compliant with keyboard navigation and screen reader support
4. **Responsive**: Mobile-first approach with breakpoints at 640px, 768px, 1024px, 1280px
5. **Consistent**: Unified design tokens and component patterns across the site

## Color System

### Brand Colors
- **Primary Gradient**: Indigo (#6366f1) to Purple (#8b5cf6)
- **Accent**: Sky Blue (#0ea5e9)
- **Secondary Gradient**: Purple to Pink (#ec4899)

### Semantic Colors
- **Success**: Green (#22c55e)
- **Warning**: Orange (#f59e0b)
- **Danger**: Red (#ef4444)

### Neutral Scale
Dark theme uses slate tones from #0a0e17 (background) to #f1f5f9 (foreground).

## Typography

### Font Stack
- **Sans-serif**: Inter (variable font) with system fallbacks
- **Monospace**: System monospace stack for code

### Type Scale
- **xs**: 0.75rem (12px)
- **sm**: 0.875rem (14px)
- **md**: 1rem (16px) - base
- **lg**: 1.125rem (18px)
- **xl**: 1.25rem (20px)
- **2xl**: 1.5rem (24px)
- **3xl**: 1.875rem (30px)
- **4xl**: 2.25rem (36px)
- **5xl**: 3rem (48px)
- **6xl**: 3.75rem (60px)
- **7xl**: 4.5rem+ (responsive)

### Weights
- Regular: 400
- Medium: 500
- Semibold: 600
- Bold: 700
- Extrabold: 800

## Spacing System

Based on 4px scale (0.25rem):
- 1: 2px
- 2: 4px
- 3: 6px
- 4: 8px
- 5: 12px
- 6: 16px
- 7: 20px
- 8: 24px
- 10: 32px
- 12: 40px
- 14: 48px
- 16: 64px
- 20: 80px
- 24: 96px

## Border Radius

- **xs**: 2px
- **sm**: 4px
- **md**: 8px
- **lg**: 12px
- **xl**: 16px
- **2xl**: 24px
- **pill**: 9999px
- **circle**: 50%

## Shadows

### Standard Shadows
- **sm**: Subtle elevation
- **md**: Card hover state
- **lg**: Modal/dropdown
- **xl**: Maximum elevation
- **2xl**: Hero elements

### Special Shadows
- **colored**: Brand-tinted shadow for CTAs
- **glow**: Glowing effect for interactive elements

## Animation System

### Durations
- **fast**: 150ms - Micro-interactions
- **base**: 250ms - Standard transitions
- **slow**: 350ms - Complex animations
- **bounce**: 500ms - Playful effects

### Easings
- **standard**: cubic-bezier(0.4, 0, 0.2, 1)
- **accelerate**: cubic-bezier(0.3, 0, 1, 1)
- **decelerate**: cubic-bezier(0, 0, 0, 1)

### Animation Classes

#### Entrance Animations
- `.animate-fade-in` - Simple fade
- `.animate-fade-in-up` - Fade with upward motion
- `.animate-fade-in-down` - Fade with downward motion
- `.animate-slide-in-left` - Slide from left
- `.animate-slide-in-right` - Slide from right
- `.animate-scale-in` - Scale up

#### Scroll Animations
- `.scroll-reveal` - Basic scroll trigger
- `.scroll-reveal-up` - Scroll up with delay
- `.scroll-reveal-scale` - Scale on scroll

Add `.delay-{100-500}` for staggered animations.

#### Utility Animations
- `.animate-pulse` - Breathing effect
- `.animate-spin` - Loading spinner
- `.animate-float` - Gentle floating motion

### Reduced Motion
All animations automatically respect `prefers-reduced-motion` preference.

## Components

### Buttons

#### Variants
```astro
<button class="button button--primary">Primary</button>
<button class="button button--secondary">Secondary</button>
<button class="button button--outline">Outline</button>
<button class="button button--ghost">Ghost</button>
```

#### Sizes
```astro
<button class="button button--sm">Small</button>
<button class="button button--lg">Large</button>
```

#### States
- Hover: Lift effect with enhanced shadow
- Active: Slight press
- Focus: Visible ring (2px offset)
- Disabled: Reduced opacity

### Cards

```astro
<div class="card">Basic card</div>
<div class="card card--elevated">Elevated card</div>
<div class="card card--interactive hover-lift">Interactive card</div>
```

Cards feature:
- Subtle backdrop blur
- Gradient overlay on hover
- Smooth lift animation
- Border color transition

### Forms

```astro
<label for="input">Label</label>
<input id="input" type="text" placeholder="Placeholder" />
```

Features:
- 1.5px borders
- Smooth focus transitions
- 3px shadow on focus
- Hover state feedback

### Badges

```astro
<span class="badge">Default</span>
<span class="badge badge--primary">Primary</span>
<span class="badge badge--success">Success</span>
<span class="badge badge--warning">Warning</span>
<span class="badge badge--danger">Danger</span>
```

## Layout Components

### Hero
Enhanced hero section with:
- Animated gradient orbs
- Grid pattern overlay
- Staggered entrance animations
- Responsive two-column layout
- Code snippet with syntax styling

### Header
Sticky header featuring:
- Backdrop blur effect
- Mobile-responsive navigation
- Network status indicator
- Smooth menu transitions

### Footer
Modern footer with:
- Four-column grid (responsive)
- Social media links
- Organized navigation sections
- Copyright and meta links

### FeatureGrid
```astro
<FeatureGrid items={features} columns={3} />
```

Features:
- Scroll-triggered animations
- Icon with glow effect
- Hover lift and border transitions
- Responsive grid layout

## Best Practices

### Performance
1. Use `loading="lazy"` for images below the fold
2. Optimize SVGs (remove unnecessary attributes)
3. Prefer CSS animations over JavaScript
4. Use `will-change` sparingly
5. Minimize layout shifts with aspect ratios

### Accessibility
1. Maintain 4.5:1 contrast for body text
2. Provide visible focus indicators
3. Use semantic HTML elements
4. Include ARIA labels for icons
5. Test with keyboard navigation
6. Ensure screen reader compatibility

### Responsive Design
1. Mobile-first approach
2. Test at breakpoints: 375px, 768px, 1024px, 1440px
3. Use fluid typography with `clamp()`
4. Stack columns on mobile
5. Adjust spacing proportionally

## File Structure

```
src/styles/
├── global.css         # Base styles and resets
├── tokens.css         # Design tokens (colors, spacing, etc.)
├── theme.css          # Theme definitions and utilities
└── animations.css     # Animation keyframes and classes

src/components/
├── Header.astro       # Site header with navigation
├── Footer.astro       # Site footer
├── Hero.astro         # Homepage hero section
├── FeatureGrid.astro  # Feature cards grid
└── CTAButtons.astro   # Call-to-action button group
```

## Migration Guide

### Updating Colors
Colors are defined in `tokens.css`. Update CSS variables:

```css
:root {
  --brand-500: #your-color;
  --brand-grad-from: #gradient-start;
  --brand-grad-to: #gradient-end;
}
```

### Adding New Components
1. Create component in `src/components/`
2. Use design tokens from `tokens.css`
3. Apply animation classes where appropriate
4. Ensure responsive behavior
5. Add hover/focus states

### Customizing Animations
Edit `animations.css` to modify:
- Animation durations
- Easing functions
- Keyframe definitions
- Delay intervals

## Browser Support

- Chrome/Edge: 90+
- Firefox: 90+
- Safari: 14+
- Mobile browsers: iOS 14+, Android 90+

Progressive enhancement ensures basic functionality in older browsers.

## Resources

- [Astro Documentation](https://docs.astro.build)
- [Tailwind CSS](https://tailwindcss.com) (optional utility classes)
- [WCAG Guidelines](https://www.w3.org/WAI/WCAG21/quickref/)
- [MDN Web Docs](https://developer.mozilla.org)
