# Studio Web UI Improvements

## Overview

This document describes the enhancements made to the Animica Studio Web UI and Wallet Extension for a production-grade, cohesive experience with rock-solid connectivity.

## Studio Web UI Enhancements

### 1. TopBar Improvements

#### Links to External Resources
- **Docs**: Direct link to Animica documentation
- **Explorer**: Link to the block explorer
- **GitHub**: Link to Animica GitHub organization (https://github.com/animicaorg)

#### Enhanced Connectivity Indicators
- **RPC Latency Tracking**: Real-time latency measurements displayed in the head indicator
- **Provider Status Icons**: Visual warnings when wallet is not detected
- **Network Mismatch Alerts**: Inline indicator when wallet network differs from Studio's selected network

#### Connection States
- **Not Connected**: Clear "Connect Wallet" button
- **Connecting**: Loading state with disabled button and "Connecting…" text
- **Connected**: Shows shortened address with copy-to-clipboard functionality
- **Unavailable**: Shows "Install Wallet" message when provider is not detected

### 2. Provider Detection Banner

A prominent, animated banner appears when the Animica wallet extension is not detected:

- **Visual Design**: Gradient background with warning colors and subtle glow
- **Clear Messaging**: Explains why the wallet is needed
- **Action Button**: Links directly to wallet installation instructions
- **Dismissible**: Users can dismiss temporarily
- **Auto-hide**: Disappears when provider becomes available

**Location**: Fixed position below TopBar, centered
**z-index**: 200 (above main content, below modals)

### 3. Network Mismatch Banner

Displays when the wallet's active network doesn't match Studio's selected network:

- **Visual Design**: Accent-colored gradient with network icons
- **Clear Information**: Shows both wallet and Studio chain IDs
- **Switch Action**: Button to prompt network switch in wallet (when supported)
- **Non-blocking**: Allows continued use of Studio while showing warning
- **Dismissible**: Temporary dismissal until mismatch is resolved

**Location**: Fixed position below TopBar (or below Provider Banner)
**z-index**: 190

### 4. StatusBar Enhancements

#### RPC Diagnostics
- **Clickable Status Block**: Click to see detailed RPC diagnostics
- **Latency Display**: Real-time round-trip time for RPC calls
- **Connection Status**: Clear online/offline indicators with color-coded dots
- **Sync Status**: Shows if node is syncing or synced
- **Error Handling**: Graceful offline handling with helpful error messages

#### Compile Status
- **Interactive Diagnostics**: Click to view compilation errors/warnings
- **Gas Estimates**: Real-time gas cost estimates
- **Status Icons**: Visual indicators for idle/compiling/success/error states

### 5. Transaction Status Indicators

New `TxStatusIndicator` component for displaying transaction lifecycle:

#### States
- **Pending**: Transaction submitted but not yet included in a block
- **Confirming**: Transaction included but waiting for confirmations
- **Confirmed**: Transaction finalized
- **Failed**: Transaction execution failed
- **Rejected**: User rejected transaction in wallet

#### Features
- **Compact Mode**: Inline badge for lists and summaries
- **Full Mode**: Detailed card with hash, block number, and error messages
- **Copy Functionality**: One-click hash copying
- **Animations**: Smooth fade-in transitions
- **Accessible**: Proper ARIA labels and keyboard navigation

### 6. Design Language

#### Color Palette
- **Background**: Deep charcoal (#050914, #0b1222)
- **Surfaces**: Layered elevation with subtle transparency
- **Accents**: Neon violet (#c084fc), cyan (#22d3ee), blue (#7aa2ff)
- **Status Colors**: Green (success), orange (warning), red (danger)

#### Typography
- **Font Family**: Inter (fallback to system sans-serif)
- **Weights**: Regular (400), SemiBold (600), Bold (700)
- **Sizes**: Fluid responsive scaling with clamp()

#### Spacing
- **Base Unit**: 4px
- **Common Gaps**: 8px, 12px, 16px, 20px, 24px
- **Consistent Padding**: 10-18px for cards and panels

#### Radius
- **Controls**: 10px
- **Cards**: 12-14px
- **Pills/Badges**: 999px (fully rounded)

#### Transitions
- **Fast**: 120-150ms (hover, focus)
- **Medium**: 200-250ms (state changes, animations)
- **Easing**: `ease-out` for entrances, `ease` for general

#### Effects
- **Soft Glows**: 0-60px blur with 12-18% opacity on themed colors
- **Shadows**: Multi-layer with increasing depth (shadow-1, shadow-2, shadow-3)
- **Gradients**: Radial gradients for atmospheric backgrounds
- **Backdrop Blur**: 6px blur on modal overlays

### 7. Accessibility

- **Focus Outlines**: Clear, high-contrast focus rings on all interactive elements
- **Keyboard Navigation**: Full keyboard support for all controls
- **ARIA Labels**: Proper labeling of dynamic content
- **Skip Links**: "Skip to content" link for screen reader users
- **Color Contrast**: WCAG AA compliant contrast ratios
- **Reduced Motion**: Respects `prefers-reduced-motion` user preference

### 8. Responsive Design

- **Breakpoints**:
  - Desktop: 1200px+
  - Tablet: 720-1200px
  - Mobile: <720px

- **Adaptive Layouts**:
  - TopBar: Stacks vertically on narrow screens
  - SideBar: Collapses to icon-only on tablets, hidden on mobile
  - Banners: Stack buttons vertically on mobile
  - StatusBar: Hides less critical info on narrow screens

## Wallet Extension UI Polish

### 1. Approval Screens

#### ConnectRequest
- Enhanced visual hierarchy with clear sections
- Origin display with copy functionality
- Permission list clearly enumerated
- Context information (account, network) in styled boxes
- Improved button styling with hover/active states

#### TxRequest
- Consistent styling with ConnectRequest
- Transaction details in organized sections
- Risk indicators for unusual transactions
- Simulation results display
- Gas estimates and limits clearly shown

### 2. Styling Improvements

- **Border Radius**: Increased from 8px to 10px for softer feel
- **Padding**: More generous spacing (12-14px vs 10-12px)
- **Section Titles**: Uppercase, bold, with reduced opacity for hierarchy
- **Box Shadows**: Added subtle shadows for depth
- **Consistent Theme**: Aligned with Studio's "quantum lab" aesthetic

## Testing

### Integration Tests

Located in `studio-web/test/integration/provider-detection.test.tsx`:

- **Provider Detection**: Tests for available/unavailable states
- **Connection Flow**: Tests connect button appearance and behavior
- **Event Handling**: Tests account and chain change events
- **RPC Connectivity**: Tests RPC polling and error handling
- **Network Mismatch**: Tests mismatch detection logic

### Running Tests

```bash
# Unit tests
cd studio-web
pnpm test

# Integration tests
pnpm test integration

# E2E tests
pnpm e2e
```

## Usage Examples

### Using TxStatusIndicator

```tsx
import TxStatusIndicator from './components/TxStatusIndicator';

// Compact mode (inline badge)
<TxStatusIndicator 
  status="pending" 
  compact 
/>

// Full mode (detailed card)
<TxStatusIndicator 
  status="confirmed"
  hash="0xabc123..."
  blockNumber={12345}
  onCopyHash={() => console.log('Hash copied')}
/>

// With error
<TxStatusIndicator 
  status="failed"
  hash="0xdef456..."
  error="Insufficient gas"
/>
```

### Customizing Banners

Banners can be dismissed and will auto-reset when conditions change:

```tsx
const [providerDismissed, setProviderDismissed] = useState(false);

<ProviderBanner
  providerStatus="unavailable"
  onDismiss={() => setProviderDismissed(true)}
/>
```

## Browser Support

- Chrome/Edge: 90+
- Firefox: 88+
- Safari: 14+

## Performance Considerations

- **RPC Polling**: 5-second intervals (configurable)
- **Event Debouncing**: Provider events debounced to avoid rapid re-renders
- **Lazy Loading**: Banners only render when conditions are met
- **CSS-in-JS**: Scoped styles avoid global CSS pollution

## Future Improvements

- [ ] WebSocket subscriptions for real-time head updates
- [ ] Multi-account switcher in TopBar
- [ ] Custom RPC endpoint configuration UI
- [ ] Transaction history panel
- [ ] Advanced diagnostics modal with detailed metrics
- [ ] Notification preferences and management
- [ ] Dark/light theme toggle
- [ ] Wallet switching (when multiple providers exist)

## Contributing

When adding new UI components:

1. Follow the established design tokens (spacing, colors, typography)
2. Ensure accessibility (focus states, ARIA labels, keyboard navigation)
3. Add responsive breakpoints for mobile/tablet
4. Include hover/active/disabled states
5. Use smooth transitions (150-250ms)
6. Add unit/integration tests
7. Update this documentation

## References

- [Design System Tokens](../src/styles/tokens.css)
- [Theme Variables](../src/styles/theme.css)
- [Component Library](../src/components/)
- [State Management](../src/state/)
- [Hooks](../src/hooks/)
