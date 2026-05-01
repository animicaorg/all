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

The dev server proxies `/admin/v1` to the admin API.

- Default target: `http://localhost:4000`
- Override target: `VITE_ADMIN_API_PROXY_TARGET=http://127.0.0.1:4000 pnpm dev`

The admin web login does not use the CEX user auth service. It requires `../services/admin-api`
and an admin account in the admin tables. For the first login, start admin-api with
`ADMIN_BOOTSTRAP_SECRET` configured, then use the login page's "First-time setup" field with that
same bootstrap secret. `SESSION_SECRET` signs cookies; it is not the first-admin bootstrap secret.

## Available Pages

- `/login` - Login page
- `/` - Dashboard with live system metrics and recent audit events
- `/users` - User search, detail, balances, risk flags, freeze/unfreeze
- `/kyc` - KYC queue review and approval/rejection
- `/markets` - Market status, controls, and open-order cancellation
- `/fees` - Fee schedule list, create, edit, archive
- `/wallets` - Wallet state and asset-network transfer controls
- `/withdrawals` - Withdrawal queue approval, rejection, retry
- `/incidents` - Incident creation, status changes, and action log
- `/audit` - Searchable audit log viewer

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
