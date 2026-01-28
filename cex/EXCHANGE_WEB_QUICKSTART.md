# CEX Exchange Web UI - Quick Start Guide

## What Was Implemented

A complete public-facing trading interface for the Animica CEX has been created at `cex/apps/exchange-web`. This is a separate application from the admin console (`apps/admin-web`) and provides traders with a full-featured exchange experience.

## Quick Start

### Using the Integrated Startup Script (Recommended)

```bash
# From repository root
./cex_up
```

This will start:
- Infrastructure services (PostgreSQL, Redis, NATS)
- API Gateway on port 3000
- Admin Service on port 4000
- Admin Console on port 5173
- **Exchange Web on port 5174** ← NEW!

### Access the Exchange

Visit: **http://localhost:5174**

**Demo Login:**
- Email: any email (e.g., `demo@animica.io`)
- Password: any password

The application currently runs in demo mode and accepts any credentials.

### Stop All Services

```bash
./cex_down
```

## Features

### 📊 Markets Page
- Browse all available trading pairs
- Search and filter markets
- View 24h price changes, highs, lows, and volume
- Click any market to start trading

### 💹 Trading Page
- Real-time orderbook (bids and asks)
- Recent trades list
- Place limit and market orders
- Cancel open orders
- View available balances

### 💼 Account Page
- View all asset balances
- See total USD equivalent
- Deposit/Withdraw placeholders (coming soon)

## Architecture

```
cex/
├── apps/
│   └── exchange-web/          ← NEW Exchange UI
│       ├── src/
│       │   ├── components/    # Reusable UI components
│       │   ├── pages/         # Page components (Login, Markets, Trading, Account)
│       │   ├── lib/           # API client, auth store
│       │   └── types/         # TypeScript definitions
│       ├── package.json
│       ├── vite.config.ts
│       └── README.md          # Detailed documentation
├── services/
│   ├── api-gateway/           # Public REST API (port 3000)
│   └── admin-service/         # Admin API (port 4000)
└── packages/
    └── ...
```

## Configuration

The exchange web UI can be configured via environment variables:

```bash
# Dev server port (default: 5174)
EXCHANGE_WEB_PORT=5174

# Dev server host (default: 0.0.0.0)
# Use 0.0.0.0 for external access, 127.0.0.1 for local only
EXCHANGE_WEB_HOST=0.0.0.0

# API Gateway URL (default: http://localhost:3000)
VITE_CEX_API_URL=http://localhost:3000

# Start the services
./cex_up
```

## Current Status: Demo Mode

The Exchange Web UI is **fully functional** but uses **mock data** because the backend API endpoints haven't been implemented yet.

### What Works Now
✅ UI fully functional with mock data
✅ All pages render correctly
✅ Navigation and routing work
✅ Order entry forms work (mocked)
✅ Authentication (demo mode)
✅ Responsive design
✅ Production build verified

### What's Needed for Production
🔲 Backend API endpoints in `@cex/api-gateway`:
   - GET `/markets`
   - GET `/orderbook/:symbol`
   - GET `/trades/:symbol`
   - POST `/orders`
   - DELETE `/orders/:id`
   - GET `/me/orders`
   - GET `/me/trades`
   - GET `/me/balances`

🔲 WebSocket support for real-time data
🔲 Real authentication (replace demo mode)
🔲 Deposit/Withdraw functionality

## Development

### Manual Development Mode

```bash
# Install dependencies
cd cex
pnpm install

# Start dev server
cd apps/exchange-web
pnpm dev

# Build for production
pnpm build

# Preview production build
pnpm preview
```

### Tech Stack
- **React 18** - UI framework
- **TypeScript** - Type safety
- **Vite** - Build tool and dev server
- **Tailwind CSS** - Styling
- **React Router** - Navigation
- **TanStack Query** - Data fetching
- **Zustand** - State management
- **Axios** - HTTP client

## Screenshots

### Login Page
![Login](https://github.com/user-attachments/assets/c0e45283-8640-4204-b6b8-5f2696d6e2a2)

### Markets Page
![Markets](https://github.com/user-attachments/assets/e5ac33b3-81ec-4ce0-babf-979a06521824)

### Trading Page
![Trading](https://github.com/user-attachments/assets/aa11b1ec-72b4-4fa1-bfca-4dc542f55459)

### Account Page
![Account](https://github.com/user-attachments/assets/4368a21f-5c7e-4d1d-8584-b86b2c9728ea)

## Troubleshooting

### Port Already in Use
If you see "EADDRINUSE" errors:

```bash
# Check what's using the port
lsof -nP -iTCP:5174 -sTCP:LISTEN

# Or
ss -ltnp | grep ":5174"

# Stop the process or use a different port
EXCHANGE_WEB_PORT=5175 ./cex_up
```

### Dependencies Not Found
```bash
# Clean install
./cex_up --reset-node-modules
```

### Vite Not Found
```bash
# Ensure dependencies are installed
cd cex && pnpm install
```

## Next Steps

1. **Implement Backend APIs**: Add real endpoints to `@cex/api-gateway`
2. **Add WebSocket**: For real-time orderbook and trade updates
3. **Real Auth**: Integrate with `@cex/auth-service`
4. **Testing**: Add unit and integration tests
5. **Deployment**: Configure for production deployment

## Support

For more details, see:
- Exchange Web README: `cex/apps/exchange-web/README.md`
- CEX Infrastructure: `cex/README.md`
- Main Repository: `README.md`

## Summary

The CEX Exchange Web UI is **complete and functional** with mock data. All UI features work, and the application is ready for backend integration. Once the API Gateway implements the required endpoints, the exchange will be fully operational.
