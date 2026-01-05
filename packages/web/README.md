# Animica Compute Platform - Web Application

**Full-featured React dashboard** for the Animica Compute + LLM Cloud Platform with GPU-powered inference, code workspaces, and blockchain payments.

## 🌟 Features

### Core Functionality
- **🤖 Chat Dashboard**: Interactive LLM chat with streaming responses, multiple models, conversation history
- **💻 Code Workspace**: Monaco-based IDE with AI assistance, file tree, terminal, GitHub integration
- **📊 Admin Dashboard**: Real-time system monitoring, usage analytics, service health
- **💳 Billing & Payments**: ANM token integration, Stripe/PayPal support, usage tracking
- **⚙️ Settings**: Profile management, API keys, team administration, security settings

### Technical Stack
- **React 18** + **TypeScript** - Modern UI with type safety
- **Vite** - Lightning-fast build tool and dev server
- **TanStack Query** - Powerful data fetching and caching
- **Zustand** - Lightweight state management
- **Tailwind CSS** - Utility-first styling
- **Monaco Editor** - VS Code-powered code editing
- **Axios** - HTTP client with interceptors

## 🚀 Quick Start

### Prerequisites
- Node.js 20+
- pnpm 9.0.0+
- Backend services running (see docker-compose.compute.yml)

### Installation

```bash
cd packages/web
pnpm install
```

### Development

```bash
# Start dev server
pnpm dev

# Open browser at http://localhost:3000
```

The dev server includes:
- Hot module replacement (HMR)
- API proxy to backend at `/api` → `http://localhost:8000/v1`
- TypeScript type checking
- ESLint linting

### Build for Production

```bash
pnpm build    # Creates optimized bundle in dist/
pnpm preview  # Preview production build locally
```

### Testing

```bash
pnpm test     # Run unit tests
pnpm lint     # Run ESLint
```

## 📁 Project Structure

```
src/
├── api/              # API client and service modules
│   ├── client.ts     # Axios instance with auth interceptors
│   ├── auth.ts       # Authentication endpoints
│   └── chat.ts       # Chat/inference endpoints
├── components/       # Reusable UI components
│   ├── Layout/       # App shell (Sidebar, TopBar)
│   ├── Chat/         # Chat-specific components
│   ├── Workspace/    # Workspace components
│   └── Common/       # Shared components
├── pages/            # Page components (one per route)
│   ├── Auth/         # Login, Register
│   ├── Dashboard/    # Main dashboard
│   ├── Chat/         # Chat interface
│   ├── Workspace/    # Code editor
│   ├── Models/       # Model selection
│   ├── Billing/      # Billing & usage
│   ├── Settings/     # User settings
│   └── Admin/        # Admin panel
├── stores/           # Zustand state stores
│   ├── authStore.ts      # Auth state & tokens
│   ├── chatStore.ts      # Chat conversations
│   └── workspaceStore.ts # Workspace sessions
├── types/            # TypeScript type definitions
│   └── index.ts      # Shared types (User, Message, etc.)
├── hooks/            # Custom React hooks (future)
├── utils/            # Utility functions (future)
├── App.tsx           # Root component with routing
├── main.tsx          # React entry point
└── index.css         # Global styles + Tailwind
```

## 🔧 Configuration

### Environment Variables

Copy `.env.example` to `.env.local`:

```bash
# API configuration
VITE_API_URL=http://localhost:8000/v1
VITE_WS_URL=ws://localhost:8000

# App settings
VITE_APP_NAME=Animica Compute Platform
VITE_APP_ENV=development
```

### API Proxy (Development)

The dev server automatically proxies `/api/*` to the backend:
```typescript
// vite.config.ts
proxy: {
  '/api': {
    target: 'http://localhost:8000',
    changeOrigin: true,
    rewrite: (path) => path.replace(/^\/api/, '/v1'),
  },
}
```

## 🎨 Key Components

### Authentication Flow
1. User logs in via email/password or wallet signature
2. JWT tokens stored in Zustand with localStorage persistence
3. Axios interceptor adds `Authorization` header to all requests
4. Auto-refresh on 401 errors

### Chat System
- Real-time streaming via Server-Sent Events (SSE)
- Conversation management with local state
- Model selection (Llama 3, GPT-4, Claude 3, etc.)
- Message history with scrolling

### Code Workspace
- Monaco editor with syntax highlighting
- File tree navigation
- Terminal output panel
- AI code suggestions panel
- GitHub integration (connect repos, open PRs)

### Billing Integration
- ANM token payments (wallet connect)
- Stripe subscriptions (Starter/Pro/Enterprise)
- PayPal one-time credits
- Real-time usage tracking

## 🐳 Docker Deployment

### Build Image

```bash
docker build -t animica/compute-web:latest .
```

### Run Container

```bash
docker run -d \
  -p 3000:80 \
  --name compute-web \
  animica/compute-web:latest
```

### Kubernetes Deployment

See `ops/k8s/web-deployment.yaml` for production Kubernetes manifests.

## 🔒 Security Features

- JWT authentication with refresh tokens
- CORS protection
- XSS protection headers
- CSRF tokens (future)
- API rate limiting (handled by backend)
- Content Security Policy (CSP)

## 📊 Performance

- Code splitting by route
- Lazy loading of heavy components (Monaco editor)
- Image optimization
- Gzip compression (nginx)
- CDN-ready static assets

## 🧪 Testing Strategy

- **Unit Tests**: Component testing with Vitest
- **Integration Tests**: API client tests
- **E2E Tests**: Playwright (future)

## 🛠️ Development Tools

### VS Code Extensions (Recommended)
- ESLint
- Prettier
- Tailwind CSS IntelliSense
- TypeScript Vue Plugin (Volar)

### Browser DevTools
- React Developer Tools
- Redux DevTools (works with Zustand)

## 📚 API Documentation

The web app consumes the following API endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/auth/login` | POST | Email/password login |
| `/v1/auth/register` | POST | Create account |
| `/v1/auth/wallet/verify` | POST | Wallet signature auth |
| `/v1/chat/completions` | POST | Stream chat responses |
| `/v1/conversations` | GET | List conversations |
| `/v1/models` | GET | Available models |
| `/v1/billing/usage` | GET | Usage statistics |

See API Gateway docs at `http://localhost:8000/docs` for full spec.

## 🚧 Roadmap

- [ ] Real-time collaboration (WebSocket)
- [ ] Prompt template library
- [ ] Advanced code diff viewer
- [ ] GitHub PR automation UI
- [ ] Model evaluation dashboard
- [ ] Contributor node monitoring
- [ ] Mobile responsive design
- [ ] Dark/light theme toggle
- [ ] Internationalization (i18n)

## 🤝 Contributing

1. Follow the existing code style (ESLint + Prettier)
2. Write TypeScript (no `any` types)
3. Add tests for new features
4. Update documentation

## 📄 License

See LICENSE.txt in the repository root.
