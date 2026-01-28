# Animica Exchange Web

Public trading interface for the Animica CEX (Centralized Exchange).

## Features

- **Markets**: Browse all available trading pairs with real-time price updates
- **Trading**: Place limit and market orders with live orderbook and recent trades
- **Account**: View balances and manage your account
- **Authentication**: Secure login system (currently in demo mode)

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
- API Gateway (port 3000)
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
```

### Environment Variables

The following environment variables can be configured:

- `VITE_CEX_API_URL` - API Gateway URL (default: http://localhost:3000)
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
- **Zustand** - State management
- **Axios** - HTTP client

### Project Structure

```
src/
├── components/     # Reusable UI components
│   └── Layout.tsx
├── pages/          # Page components
│   ├── LoginPage.tsx
│   ├── MarketsPage.tsx
│   ├── TradingPage.tsx
│   └── AccountPage.tsx
├── lib/            # Utilities and configuration
│   ├── api-client.ts
│   └── auth-store.ts
├── types/          # TypeScript type definitions
│   └── index.ts
├── hooks/          # Custom React hooks
├── App.tsx         # Main app component
├── main.tsx        # Entry point
└── index.css       # Global styles
```

### API Integration

The app communicates with the API Gateway at `http://localhost:3000` (configurable via `VITE_CEX_API_URL`).

**Current Status**: The UI is using mock data. Real API endpoints need to be implemented in `@cex/api-gateway`.

**Required Endpoints** (to be implemented):
- `GET /markets` - List all markets
- `GET /orderbook/:symbol` - Get orderbook for a symbol
- `GET /trades/:symbol` - Get recent trades
- `POST /orders` - Create a new order
- `DELETE /orders/:id` - Cancel an order
- `GET /me/orders` - Get user's open orders
- `GET /me/trades` - Get user's trade history
- `GET /me/balances` - Get user's balances

**WebSocket** (future):
- Real-time orderbook updates
- Real-time trade stream
- Order status updates

## Demo Mode

Currently, the app runs in demo mode:
- Any email/password combination will log you in
- Market data is mocked
- Orders are accepted but not actually executed
- Balances are static mock data

This allows frontend development and testing without a fully functional backend.

## Production Considerations

Before deploying to production:

1. **Authentication**: Implement real authentication with the backend
2. **API Endpoints**: Ensure all required endpoints are implemented in API Gateway
3. **WebSocket**: Add WebSocket support for real-time data
4. **Error Handling**: Add comprehensive error handling and user feedback
5. **Security**: Implement CSRF protection, rate limiting, etc.
6. **Performance**: Add proper caching strategies
7. **Monitoring**: Add error tracking (e.g., Sentry)
8. **Testing**: Add unit and integration tests

## Contributing

This is part of the Animica monorepo. Follow the standard contribution guidelines.
