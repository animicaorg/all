# Admin Web Console

React-based admin console for Animica CEX operations.

## Features

- 🔐 Secure authentication with TOTP
- 👥 User management
- ✅ KYC review workflow
- 📊 Market controls
- 💰 Fee management
- 💳 Withdrawal approvals
- 🚨 Incident management
- 📜 Audit log viewer
- 🔒 RBAC-based UI visibility

## Development

```bash
# Install dependencies
pnpm install

# Start dev server (with API proxy)
pnpm dev

# Build for production
pnpm build

# Preview production build
pnpm preview
```

## Architecture

- **Framework**: React 18 + TypeScript
- **Build Tool**: Vite
- **Routing**: React Router v6
- **State Management**: Zustand + React Query
- **Styling**: Tailwind CSS
- **Icons**: Lucide React
- **API Client**: Axios

## Project Structure

```
src/
├── components/     # Reusable UI components
├── contexts/       # React contexts (auth, etc.)
├── pages/          # Page components
├── services/       # API client and services
├── types/          # TypeScript type definitions
├── utils/          # Utility functions
├── App.tsx         # Main app component
└── main.tsx        # Entry point
```

## Environment

The dev server proxies `/admin/v1` to `http://localhost:4000` (admin-api).

## Available Pages

- `/login` - Login page
- `/` - Dashboard
- `/users` - User management
- `/kyc` - KYC review (TODO)
- `/markets` - Market controls (TODO)
- `/fees` - Fee management (TODO)
- `/wallets` - Wallet status (TODO)
- `/withdrawals` - Withdrawal approvals (TODO)
- `/incidents` - Incident management (TODO)
- `/audit` - Audit log viewer (TODO)

## Security

- All routes except `/login` require authentication
- JWT tokens stored in localStorage
- Refresh token rotation on expiry
- HttpOnly cookies for enhanced security (via API)
- CSRF protection (via API)

## Testing Credentials

After running the seed script in admin-api:

- Email: `admin@animica.io`
- Password: `Admin123!`
- TOTP: Add the secret to your authenticator app

## License

Apache 2.0
