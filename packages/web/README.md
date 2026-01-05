# Animica Compute Platform - Web Application

React/TypeScript web application for the Animica Compute Platform.

## Features

- **Chat Dashboard**: Interactive LLM chat interface with streaming responses
- **Code Workspace**: Monaco-based code editor with AI assistance
- **Admin Dashboards**: Usage analytics, billing, and system monitoring
- **User Settings**: Profile management, API keys, payment methods

## Tech Stack

- **React 18**: UI library
- **TypeScript**: Type safety
- **Vite**: Build tool and dev server
- **TanStack Query**: Data fetching and caching
- **Zustand**: State management
- **Tailwind CSS**: Styling
- **Monaco Editor**: Code editing

## Development

### Setup

```bash
cd packages/web
pnpm install
```

### Run Dev Server

```bash
pnpm dev
```

Opens at http://localhost:3000

### Build for Production

```bash
pnpm build
```

### Run Tests

```bash
pnpm test
```

## Project Structure

```
src/
├── components/      # Reusable UI components
├── pages/          # Page components
├── hooks/          # Custom React hooks
├── stores/         # Zustand stores
├── api/            # API client functions
├── types/          # TypeScript type definitions
└── utils/          # Utility functions
```

## Environment Variables

Create `.env.local`:

```
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000
```

## Features

### Chat Dashboard

- Multiple conversation threads
- Real-time token streaming
- Model selection
- Conversation history
- Export/import chats

### Code Workspace

- Multi-language support
- Code execution in sandboxes
- AI-powered code completion
- Git integration
- Collaborative editing (future)

### Admin Dashboard

- Usage metrics
- Billing overview
- API key management
- Team management (enterprise)

## Deployment

Docker image: `animica/compute-web:latest`

```bash
docker build -t animica/compute-web .
docker run -p 3000:80 animica/compute-web
```
