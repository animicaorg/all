# Animica Exchange Web

Public trading interface for the Animica CEX (Centralized Exchange).

## Features

### Core Functionality
- **Markets**: Browse all available trading pairs with real-time price updates via WebSocket
- **Trading**: Professional trading terminal with:
  - Real-time orderbook (WebSocket + REST fallback)
  - Recent trades stream
  - Advanced order entry with validation
  - Fee preview and risk checks
  - Percentage-based order sizing
  - Diagnostics drawer for debugging
- **Account**: View balances and manage your account
- **Authentication**: Secure login system (currently in demo mode)

### Trading Features
- **Order Types**: Limit and Market orders
- **Real-time Data**: WebSocket connections with auto-reconnect
- **Validation**: 
  - Price tick size validation
  - Order size step validation
  - Minimum order size checks
  - Insufficient balance detection
- **UX Enhancements**:
  - Toast notifications for actions
  - Loading states and error handling
  - Optimistic UI updates
  - Connection status indicators

### Technical Features
- **WebSocket Client**: Robust client with:
  - Auto-reconnect with exponential backoff
  - Ping/pong heartbeat monitoring
  - Sequence-based orderbook updates
  - Message validation with Zod
  - Multi-channel subscriptions
- **API Integration**: REST API with graceful fallbacks
- **State Management**: Zustand for WebSocket and auth state
- **Data Fetching**: TanStack Query for REST API
- **Type Safety**: Full TypeScript coverage

## Development

### Prerequisites

- Node.js 20+
- pnpm (managed via corepack)

### Quick Start

The easiest way to run the exchange web UI is using the root-level `cex_up` script:

```bash
# From repository root
./cex_up
```

This will start:
- Infrastructure (PostgreSQL, Redis, NATS)
- API Gateway (port 3000) - REST + WebSocket
- Admin Service (port 4000)
- Admin Console (port 5173)
- **Exchange Web (port 5174)**

Then visit: http://localhost:5174

### Manual Development

To run the exchange web UI independently:

```bash
# Install dependencies
pnpm install

# Start dev server
pnpm dev

# Build for production
pnpm build

# Preview production build
pnpm preview

# Type check
pnpm type-check
```

### Environment Variables

The following environment variables can be configured:

- `VITE_CEX_API_URL` - REST API Gateway URL (default: http://localhost:3000)
- `VITE_CEX_WS_URL` - WebSocket URL (default: ws://localhost:3000/ws)
- `PORT` - Dev server port (default: 5174)
- `HOST` - Dev server host (default: 127.0.0.1)

## Architecture

### Tech Stack

- **React 18** - UI framework
- **TypeScript** - Type safety
- **Vite** - Build tool and dev server
- **Tailwind CSS** - Styling
- **React Router** - Routing
- **TanStack Query** - Data fetching and caching
- **Zustand** - State management (WebSocket, auth)
- **Axios** - HTTP client
- **Zod** - Runtime validation
- **React Hot Toast** - Notifications

### Project Structure

```
src/
├── components/          # Reusable UI components
│   ├── Layout.tsx
│   ├── WSProvider.tsx   # WebSocket connection manager
│   └── OrderEntry.tsx   # Order form with validation
├── pages/               # Page components
│   ├── LoginPage.tsx
│   ├── MarketsPage.tsx
│   ├── TradingPage.tsx  # Main trading interface
│   └── AccountPage.tsx
├── lib/                 # Core utilities
│   ├── api-client.ts    # REST API client
│   ├── auth-store.ts    # Auth state (Zustand)
│   ├── ws-client.ts     # WebSocket client
│   ├── ws-store.ts      # WebSocket state (Zustand)
│   └── ws-types.ts      # WebSocket message types
├── types/               # TypeScript type definitions
│   └── index.ts
├── App.tsx              # Main app component
├── main.tsx             # Entry point
└── index.css            # Global styles
```

### API Integration

The app communicates with the API Gateway at `http://localhost:3000` (configurable via `VITE_CEX_API_URL`).

**REST Endpoints**:
- `GET /meta` - Capabilities and feature detection
- `GET /markets` - List all markets with 24h stats
- `GET /markets/:symbol/orderbook` - Get orderbook snapshot
- `GET /markets/:symbol/trades` - Get recent trades
- `GET /markets/:symbol/candles` - Get candlestick data
- `POST /orders` - Create a new order (with idempotency)
- `DELETE /orders/:id` - Cancel an order
- `GET /me/orders` - Get user's open orders
- `GET /me/trades` - Get user's trade history
- `GET /me/balances` - Get user's balances

**WebSocket Channels** (`ws://localhost:3000/ws`):
- `orderbook` - Real-time orderbook updates (snapshot + deltas)
- `trades` - Real-time trade stream
- `ticker` - Real-time 24h ticker data
- `user_orders` - User-specific order updates (requires auth)
- `user_trades` - User-specific trade updates (requires auth)

### WebSocket Protocol

**Subscribe**:
```json
{
  "action": "subscribe",
  "channel": "orderbook",
  "symbol": "ANM-USDT"
}
```

**Unsubscribe**:
```json
{
  "action": "unsubscribe",
  "channel": "orderbook",
  "symbol": "ANM-USDT"
}
```

**Snapshot Message**:
```json
{
  "type": "snapshot",
  "channel": "orderbook",
  "symbol": "ANM-USDT",
  "data": {
    "bids": [[1.25, 100], [1.24, 200]],
    "asks": [[1.26, 150], [1.27, 180]],
    "sequence": 12345
  },
  "timestamp": 1706453020000
}
```

**Update Message**:
```json
{
  "type": "update",
  "channel": "orderbook",
  "symbol": "ANM-USDT",
  "data": {
    "bids": [[1.25, 0]],  // 0 quantity = remove
    "asks": [[1.26, 200]], // update quantity
    "sequence": 12346
  },
  "timestamp": 1706453021000
}
```

## Feature Flags & Capabilities

The UI calls `GET /meta` on startup to detect backend capabilities and gracefully degrades:

- If WebSocket not available, falls back to REST polling
- If orderbook endpoint fails, shows loading state
- If order submission fails, shows clear error message
- All features are designed to work with partial backend implementation

## Diagnostics

Click the Activity icon (⚡) in the trading page header to open the diagnostics drawer:

- **Connection State**: WebSocket connection status
- **Subscriptions**: Active channel subscriptions
- **Orderbook Sequence**: Current sequence number (for gap detection)
- **Latency**: WebSocket ping/pong latency

## Demo Mode

Currently, the app runs in demo mode:
- Any email/password combination will log you in
- Mock data is used as fallback when backend is unavailable
- Orders may be accepted but not actually executed (depending on backend)
- Balances are seeded with test amounts

This allows frontend development and testing without a fully functional backend.

## Production Considerations

Before deploying to production:

1. **Authentication**: Implement real authentication with JWT/sessions
2. **API Endpoints**: Ensure all endpoints are implemented and tested
3. **WebSocket**: Production-grade WebSocket server with load balancing
4. **Error Handling**: Comprehensive error tracking (e.g., Sentry)
5. **Security**: 
   - Implement CSRF protection
   - Rate limiting on API endpoints
   - CSP headers
   - HttpOnly cookies for sessions
6. **Performance**: 
   - CDN for static assets
   - Server-side rendering (if needed)
   - Redis caching for market data
7. **Monitoring**: Application performance monitoring
8. **Testing**: Unit tests, integration tests, E2E tests

## Troubleshooting

### WebSocket not connecting
- Check that API Gateway is running on port 3000
- Verify `VITE_CEX_WS_URL` environment variable
- Check browser console for connection errors
- Ensure firewall allows WebSocket connections

### Orders not submitting
- Check authentication (must be logged in)
- Verify balance is sufficient
- Check tick size and step size validation
- Look for error messages in toast notifications
- Check API Gateway logs for errors

### Data not updating
- Check WebSocket connection status (diagnostics drawer)
- Verify subscriptions are active
- Check orderbook sequence numbers for gaps
- Fallback to REST API should work if WS fails

## Contributing

This is part of the Animica monorepo. Follow the standard contribution guidelines.

## License

Apache-2.0
