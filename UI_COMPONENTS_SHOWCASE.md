# UI Components Showcase

This document showcases the new UI components added to the Animica frontend apps.

---

## ErrorBoundary Component

### Purpose
Catches unhandled React errors and displays a graceful fallback UI instead of a blank screen.

### Usage

```tsx
import { ErrorBoundary } from './components/ErrorBoundary';

function App() {
  return (
    <ErrorBoundary>
      <YourApp />
    </ErrorBoundary>
  );
}
```

### Features
- ✅ Catches all React errors in the component tree
- ✅ Shows user-friendly error message
- ✅ Optional technical details (collapsed by default, dev-only)
- ✅ "Try Again" button to reset error state
- ✅ Custom fallback UI support

### Visual Example

```
┌─────────────────────────────────────────┐
│                                         │
│              ⚠️                          │
│                                         │
│       Something went wrong              │
│                                         │
│  Cannot read property 'map' of         │
│  undefined                              │
│                                         │
│  [▶ Error Details (Dev Only)]          │
│                                         │
│        [ Try Again ]                    │
│                                         │
└─────────────────────────────────────────┘
```

---

## LoadingSpinner Component

### Purpose
Displays an animated spinner for loading states.

### Usage

```tsx
import { LoadingSpinner } from './components/LoadingStates';

function MyComponent() {
  const { data, loading } = useQuery();
  
  if (loading) {
    return <LoadingSpinner size="md" label="Loading data..." />;
  }
  
  return <div>{data}</div>;
}
```

### Sizes
- `sm` - 20px (for inline, buttons)
- `md` - 32px (default, for sections)
- `lg` - 48px (for full-page loading)

### Visual Example

```
     ⟳    Loading data...
   (spinning ring)
```

---

## LoadingSkeleton Component

### Purpose
Shows placeholder bars with shimmer animation for content that's loading.

### Usage

```tsx
import { LoadingSkeleton } from './components/LoadingStates';

function MyComponent() {
  const { data, loading } = useQuery();
  
  if (loading) {
    return (
      <div>
        <LoadingSkeleton width="40%" height="24px" variant="rect" />
        <LoadingSkeleton count={3} />
      </div>
    );
  }
  
  return <div>{data}</div>;
}
```

### Variants
- `text` - For text content (rounded corners)
- `rect` - For rectangular blocks (cards, buttons)
- `circle` - For avatars, icons

### Visual Example

```
┌─────────────────────────────────────────┐
│ ▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░       │  (shimmer animation)
│                                         │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░       │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░       │
│ ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░       │
└─────────────────────────────────────────┘
```

---

## LoadingCard Component

### Purpose
Pre-composed card skeleton for consistent loading states.

### Usage

```tsx
import { LoadingCard } from './components/LoadingStates';

function MyList() {
  const { data, loading } = useQuery();
  
  if (loading) {
    return (
      <>
        <LoadingCard lines={4} hasHeader />
        <LoadingCard lines={4} hasHeader />
        <LoadingCard lines={4} hasHeader />
      </>
    );
  }
  
  return data.map(item => <Card key={item.id} {...item} />);
}
```

### Visual Example

```
┌─────────────────────────────────────────┐
│ ▓▓▓▓▓▓▓▓▓░░░░░░░░░░   Header           │
├─────────────────────────────────────────┤
│                                         │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░         │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░           │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░               │
│                                         │
└─────────────────────────────────────────┘
```

---

## LoadingOverlay Component

### Purpose
Full-screen loading overlay for blocking operations.

### Usage

```tsx
import { LoadingOverlay } from './components/LoadingStates';

function MyComponent() {
  const [deploying, setDeploying] = useState(false);
  
  return (
    <>
      <LoadingOverlay visible={deploying} label="Deploying contract..." />
      <button onClick={() => deploy()}>Deploy</button>
    </>
  );
}
```

### Visual Example

```
┌─────────────────────────────────────────┐
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
│░░░░░  ┌──────────────────┐  ░░░░░░░░░░│
│░░░░░  │                  │  ░░░░░░░░░░│
│░░░░░  │       ⟳          │  ░░░░░░░░░░│
│░░░░░  │ Deploying...     │  ░░░░░░░░░░│
│░░░░░  │                  │  ░░░░░░░░░░│
│░░░░░  └──────────────────┘  ░░░░░░░░░░│
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
└─────────────────────────────────────────┘
```

---

## EmptyState Component

### Purpose
Displays a friendly message when there's no data.

### Usage

```tsx
import { EmptyState } from './components/LoadingStates';

function MyList() {
  const { data } = useQuery();
  
  if (data.length === 0) {
    return (
      <EmptyState
        icon="📭"
        title="No transactions yet"
        message="Your transaction history will appear here"
        action={{
          label: "Make a transaction",
          onClick: () => navigate('/send')
        }}
      />
    );
  }
  
  return <List data={data} />;
}
```

### Visual Example

```
┌─────────────────────────────────────────┐
│                                         │
│                  📭                      │
│                                         │
│          No transactions yet            │
│                                         │
│  Your transaction history will          │
│  appear here                            │
│                                         │
│      [ Make a transaction ]             │
│                                         │
└─────────────────────────────────────────┘
```

---

## ErrorDisplay Component

### Purpose
Shows user-friendly error messages with optional retry and details.

### Usage

```tsx
import { ErrorDisplay } from './components/ErrorDisplay';

function MyComponent() {
  const { data, error, refetch } = useQuery();
  
  if (error) {
    return (
      <ErrorDisplay
        title="Failed to load data"
        message="Could not connect to the server"
        details={error.stack}
        onRetry={refetch}
        severity="error"
      />
    );
  }
  
  return <div>{data}</div>;
}
```

### Severity Levels
- `error` - Red theme for failures
- `warning` - Yellow theme for warnings
- `info` - Blue theme for information

### Visual Example

```
┌─────────────────────────────────────────┐
│ ❌  Failed to load data                 │
│                                         │
│     Could not connect to the server     │
│                                         │
│     [▶ Show Details]                    │
│                                         │
│     [ Try Again ]                       │
└─────────────────────────────────────────┘
```

With details expanded:

```
┌─────────────────────────────────────────┐
│ ❌  Failed to load data                 │
│                                         │
│     Could not connect to the server     │
│                                         │
│     [▼ Show Details]                    │
│     ┌─────────────────────────────┐     │
│     │ Error: Network timeout      │     │
│     │   at fetch (/src/api.ts:42) │     │
│     │   at useQuery (/hooks:18)   │     │
│     └─────────────────────────────┘     │
│                                         │
│     [ Try Again ]                       │
└─────────────────────────────────────────┘
```

---

## NetworkErrorBanner Component

### Purpose
Specific banner for RPC/WebSocket connection issues.

### Usage

```tsx
import { NetworkErrorBanner } from './components/ErrorDisplay';

function MyApp() {
  const { rpcUrl, wsUrl, isConnected } = useNetwork();
  
  if (!isConnected) {
    return (
      <NetworkErrorBanner
        rpcUrl={rpcUrl}
        wsUrl={wsUrl}
        onOpenSettings={() => navigate('/settings/network')}
      />
    );
  }
  
  return <YourApp />;
}
```

### Visual Example

```
┌─────────────────────────────────────────────────────────┐
│ ⚠️  Network Connection Issue                           │
│                                                         │
│     Unable to reach the RPC endpoint:                  │
│     http://localhost:8545                              │
│     WebSocket: ws://localhost:8546                     │
│                                                         │
│                            [ Network Settings ]         │
└─────────────────────────────────────────────────────────┘
```

---

## Color & Style Guidelines

### Color Palette (CSS Variables)

```css
/* Primary colors */
--color-accent: /* Brand color (purple/blue) */
--color-on-accent: /* Text on accent background */

/* Status colors */
--color-success: /* Green for success states */
--color-warning: /* Yellow for warnings */
--color-danger: /* Red for errors */

/* Surfaces */
--color-bg: /* Main background */
--color-surface: /* Card/panel background */
--color-border: /* Border color */

/* Text */
--color-text: /* Primary text */
--color-text-muted: /* Secondary text */
--color-text-strong: /* Headings/emphasis */
```

### Typography Scale

```css
--text-xs: clamp(0.75rem, 0.72rem + 0.15vw, 0.82rem)
--text-sm: clamp(0.875rem, 0.84rem + 0.2vw, 0.95rem)
--text-md: clamp(1rem, 0.96rem + 0.25vw, 1.1rem)      /* Default */
--text-lg: clamp(1.125rem, 1.06rem + 0.35vw, 1.25rem)
--text-xl: clamp(1.25rem, 1.15rem + 0.45vw, 1.5rem)
--text-2xl: clamp(1.5rem, 1.35rem + 0.7vw, 1.875rem)
--text-3xl: clamp(1.875rem, 1.65rem + 1vw, 2.25rem)
--text-4xl: clamp(2.25rem, 2rem + 1.2vw, 3rem)
```

### Spacing Scale (4px base)

```css
--space-1: 0.25rem  /* 4px */
--space-2: 0.5rem   /* 8px */
--space-3: 0.75rem  /* 12px */
--space-4: 1rem     /* 16px */
--space-5: 1.25rem  /* 20px */
--space-6: 1.5rem   /* 24px */
--space-8: 2rem     /* 32px */
--space-10: 2.5rem  /* 40px */
--space-12: 3rem    /* 48px */
```

### Border Radius

```css
--radius-sm: 6px   /* Small buttons, tags */
--radius-md: 10px  /* Default buttons, inputs */
--radius-lg: 14px  /* Cards, modals */
--radius-pill: 999px /* Badges, pills */
```

### Shadows

```css
--shadow-sm: 0 1px 2px rgba(0,0,0,0.05), 0 1px 3px rgba(0,0,0,0.1)
--shadow-md: 0 4px 6px rgba(0,0,0,0.07), 0 2px 4px rgba(0,0,0,0.06)
--shadow-lg: 0 10px 15px rgba(0,0,0,0.1), 0 4px 6px rgba(0,0,0,0.05)
--shadow-card: 0 4px 16px rgba(0,0,0,0.08)
--focus-ring: 0 0 0 3px rgba(99, 102, 241, 0.35)
```

---

## Animation Guidelines

### Transitions

```css
--transition-fast: 120ms    /* Hover, focus */
--transition-medium: 200ms  /* Modals, dropdowns */
--transition-slow: 320ms    /* Page transitions */
```

### Hover Effects (Buttons)

```tsx
<button className="btn">
  {/* On hover: translateY(-1px) + enhanced shadow */}
  {/* On active: translateY(0) */}
  Click me
</button>
```

### Shimmer Animation (Skeletons)

```css
@keyframes skeleton-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
/* Duration: 1.5s, easing: ease-in-out */
```

---

## Accessibility

### Keyboard Navigation
- All interactive elements are focusable
- Focus rings are visible (2px outline)
- Skip to content links where appropriate

### Screen Readers
- `role="status"` on loading states
- `role="alert"` on error states
- `aria-label` on icon buttons
- `aria-live="polite"` on toast notifications

### Color Contrast
- Text on backgrounds: 4.5:1 minimum
- Large text (18px+): 3:1 minimum
- Interactive elements: 3:1 minimum

---

## Responsive Breakpoints

```css
/* Mobile first approach */
@media (max-width: 640px) {
  /* Mobile: stacked layouts, full-width elements */
}

@media (max-width: 768px) {
  /* Tablet: 2-column grids, collapsed navigation */
}

@media (max-width: 1024px) {
  /* Desktop: full feature set, side-by-side layouts */
}

@media (max-width: 1280px) {
  /* Wide desktop: optimal spacing */
}
```

---

## Dark Mode Support

All components support dark mode via CSS variables:

```tsx
// Toggle dark mode
document.documentElement.setAttribute('data-theme', 'dark');

// Or via class
document.documentElement.classList.add('dark');
```

Variables automatically switch between light/dark values:

```css
:root {
  --color-bg: #ffffff;
  --color-text: #000000;
}

:root[data-theme="dark"] {
  --color-bg: #0b1021;
  --color-text: #ffffff;
}
```

---

## Best Practices

### Error Handling
1. Always show user-friendly messages
2. Provide retry mechanisms
3. Include technical details in dev mode only
4. Use appropriate severity levels

### Loading States
1. Show loading immediately on user action
2. Disable interactive elements during loading
3. Use skeletons for content that will appear
4. Use spinners for actions that don't reveal structure

### Network Awareness
1. Always display current network prominently
2. Show connection status visually (green/red dot)
3. Provide clear error messages when disconnected
4. Link to settings for configuration

### Consistency
1. Use the same component APIs across apps
2. Follow the design token system
3. Maintain spacing and sizing consistency
4. Use semantic HTML elements

---

## Integration Checklist

When adding these components to an app:

- [ ] Wrap root App component with `ErrorBoundary`
- [ ] Replace raw loading text with `LoadingSpinner`
- [ ] Add `LoadingSkeleton` to list views
- [ ] Replace raw error messages with `ErrorDisplay`
- [ ] Add `NetworkErrorBanner` for RPC errors
- [ ] Use `EmptyState` for no-data scenarios
- [ ] Add `LoadingOverlay` for blocking operations
- [ ] Test all loading and error states
- [ ] Verify dark mode support
- [ ] Check mobile responsiveness
- [ ] Add keyboard navigation
- [ ] Verify screen reader support

---

## Example: Complete Component Integration

```tsx
import { ErrorBoundary } from './components/ErrorBoundary';
import { LoadingSpinner, EmptyState } from './components/LoadingStates';
import { ErrorDisplay, NetworkErrorBanner } from './components/ErrorDisplay';
import { useQuery } from './hooks/useQuery';
import { useNetwork } from './hooks/useNetwork';

function MyFeature() {
  const { data, loading, error, refetch } = useQuery('/api/data');
  const { isConnected, rpcUrl } = useNetwork();
  
  // Network error
  if (!isConnected) {
    return (
      <NetworkErrorBanner
        rpcUrl={rpcUrl}
        onOpenSettings={() => navigate('/settings')}
      />
    );
  }
  
  // Loading state
  if (loading) {
    return <LoadingSpinner size="lg" label="Loading data..." />;
  }
  
  // Error state
  if (error) {
    return (
      <ErrorDisplay
        message="Failed to load data"
        details={error.message}
        onRetry={refetch}
        severity="error"
      />
    );
  }
  
  // Empty state
  if (data.length === 0) {
    return (
      <EmptyState
        icon="📭"
        title="No data available"
        message="There's nothing to show right now"
      />
    );
  }
  
  // Success state
  return (
    <div>
      {data.map(item => (
        <Card key={item.id} {...item} />
      ))}
    </div>
  );
}

// Wrap with error boundary
export default function MyFeatureWrapper() {
  return (
    <ErrorBoundary>
      <MyFeature />
    </ErrorBoundary>
  );
}
```

---

*This showcase demonstrates the comprehensive UI component library added to the Animica frontend apps.*
