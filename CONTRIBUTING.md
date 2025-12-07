# Contributing to Animica Monorepo

Thanks for contributing! This file outlines how to set up a local development environment, run tests, and submit changes.

## Table of Contents

- [Getting Started - Python/Backend](#getting-started---pythonbackend)
- [Getting Started - Frontend](#getting-started---frontend)
- [Testing](#testing)
- [Code Style](#code-style)
- [Submitting Changes](#submitting-changes)

## Getting Started - Python/Backend

1. Clone the repo and create a virtualenv for Python development:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

2. Install pre-commit hooks (recommended):

```bash
python -m pip install pre-commit
pre-commit install
pre-commit run --all-files
```

## Getting Started - Frontend

### Prerequisites

- **Node.js** ≥ 18 (LTS 20 recommended)
- **pnpm** ≥ 9.0.0

### Setup

1. Install pnpm globally:

```bash
npm install -g pnpm@9.0.0
```

2. Install all workspace dependencies:

```bash
pnpm install --no-frozen-lockfile
```

3. Configure environment variables:

Copy `.env.example` to `.env.local` in each app directory and adjust as needed:

```bash
# For miner dashboard
cp apps/miner-dashboard/.env.example apps/miner-dashboard/.env.local

# For studio web
cp studio-web/.env.example studio-web/.env.local

# For wallet extension
cp wallet-extension/.env.example wallet-extension/.env

# For explorer web
cp explorer-web/.env.example explorer-web/.env.local

# For website
cp website/.env.example website/.env.local
```

4. Start a frontend app:

```bash
# Miner Dashboard
pnpm --filter miner-dashboard dev

# Studio Web
pnpm --filter studio-web dev

# Wallet Extension
cd wallet-extension && pnpm dev

# Website
pnpm --filter animica-website dev
```

See [FRONTEND_QUICKSTART.md](./FRONTEND_QUICKSTART.md) for detailed setup instructions.

## Testing

### Python Tests

- Run unit tests:

```bash
python -m pytest -q
```

- Run a single integration test (relayer):

```bash
python -m pytest tests/integration/test_payout_relayer.py -q
```

### Frontend Tests

- Run unit tests for an app:

```bash
pnpm --filter studio-web test
pnpm --filter wallet-extension test
```

- Run E2E tests:

```bash
pnpm --filter studio-web e2e
pnpm --filter wallet-extension e2e
```

- Run all tests in a workspace:

```bash
cd studio-web
pnpm test
pnpm e2e
```

## Code Style

### Python

- Follow PEP 8 style guidelines
- Use Black for formatting (via pre-commit)
- Use Ruff for linting (via pre-commit)
- Type hints required for public APIs

### Frontend (TypeScript/JavaScript)

- Use **TypeScript** for all new code
- Follow existing code style (ESLint will enforce)
- Use **Prettier** for formatting (where configured)
- Component naming: PascalCase for components, camelCase for functions
- File naming: PascalCase for components, kebab-case for utilities

#### Frontend Style Guidelines

```typescript
// ✅ Good
export function MyComponent({ title, onSubmit }: Props) {
  const [state, setState] = useState<string>("");
  
  return (
    <div className="container">
      <h1>{title}</h1>
      <button onClick={onSubmit}>Submit</button>
    </div>
  );
}

// ✅ Good utility file
// utils/format-address.ts
export function formatAddress(addr: string): string {
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}
```

#### Design System Usage

When adding UI components:

- Use design tokens from CSS variables (see FRONTEND_UX_IMPROVEMENTS.md)
- Follow color palette: `--accent`, `--ok`, `--warn`, `--err`
- Use consistent spacing: `--space-xs`, `--space-sm`, `--space-md`, etc.
- Typography: Inter font family, base 14px
- Responsive breakpoints: 640px, 768px, 1024px, 1280px

### Working with the Flutter wallet

- See `wallet/README.md` for a dedicated quickstart.
- Use `make run-wallet` from the repo root to run the wallet helper.
- Flutter code style: Follow Dart conventions and Flutter best practices

## Build & CI

### Python/Backend

- The `pq-precompile.yml` workflow saves bench stdout to `bench_output.jsonl` and pytest junit xml to `reports/junit.xml` as artifacts for review.
- All Python tests must pass before merging

### Frontend

- All apps must build successfully: `pnpm build`
- Linting must pass: `pnpm lint` (where configured)
- Unit tests must pass: `pnpm test`
- E2E tests for critical flows must pass

## Submitting Changes

### Pull Request Process

1. **Create a feature branch**: `git checkout -b feature/your-feature-name`
2. **Make your changes**: Follow code style guidelines
3. **Write/update tests**: Add tests for new functionality
4. **Run tests locally**: Ensure all tests pass
5. **Lint your code**: Run linters and formatters
6. **Commit with clear messages**: Describe what and why, not how
7. **Push and create PR**: Target the `main` branch
8. **Address review feedback**: Respond to reviewer comments
9. **Wait for CI**: Ensure all CI checks pass

### Commit Message Guidelines

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types**:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `style`: Formatting, missing semicolons, etc.
- `refactor`: Code change that neither fixes a bug nor adds a feature
- `test`: Adding missing tests
- `chore`: Maintenance tasks

**Examples**:
```
feat(wallet): add network switcher UI

Implements network selection dropdown in wallet extension popup.
Users can now switch between devnet, testnet, and mainnet.

Closes #123
```

```
fix(explorer): handle null block responses gracefully

Previously, null responses would crash the block detail page.
Now displays a friendly error message instead.

Fixes #456
```

### Code Review Checklist

Before requesting review, ensure:

- [ ] Code builds without errors or warnings
- [ ] All tests pass (unit and E2E where applicable)
- [ ] No console errors in development
- [ ] Code follows style guidelines
- [ ] Documentation updated (if API/behavior changes)
- [ ] Environment variables documented (if new config added)
- [ ] Responsive design tested (for frontend changes)
- [ ] Accessibility considered (keyboard nav, screen readers)

### Frontend-Specific Checklist

- [ ] No hardcoded URLs or credentials
- [ ] All async operations have loading states
- [ ] Error states handled gracefully
- [ ] Network failures don't crash the app
- [ ] Configuration read from environment variables
- [ ] UI tested in both light and dark themes (where applicable)
- [ ] Mobile/tablet layouts tested
- [ ] Browser console clean (no warnings or errors)

## Documentation Requirements

### For New Features

- Update relevant README files
- Add inline code comments for complex logic
- Update FRONTEND_QUICKSTART.md if setup changes
- Add examples to `examples/` directory if applicable

### For Bug Fixes

- Add test case that reproduces the bug
- Document root cause in commit message
- Update troubleshooting section if user-facing

### For API Changes

- Update TypeScript types/interfaces
- Update SDK documentation
- Add migration guide if breaking change
- Version bump according to semver

## Getting Help

### Resources

- **Frontend Quickstart**: [FRONTEND_QUICKSTART.md](./FRONTEND_QUICKSTART.md)
- **UX Improvements Doc**: [FRONTEND_UX_IMPROVEMENTS.md](./FRONTEND_UX_IMPROVEMENTS.md)
- **App-Specific Docs**: Check each app's README.md
- **Architecture**: See `docs/architecture/` (Python/backend)

### Communication

- **Issues**: For bugs, feature requests, and questions
- **Discussions**: For general questions and ideas
- **Discord**: [Link TBD] for real-time chat

### Contact

- For infra/CI questions, open an issue or contact the maintainers listed in `MAINTAINERS.md`
- For frontend questions, tag `@frontend` in your issue/PR

## License

By contributing, you agree that your contributions will be licensed under the same license as the project (Apache 2.0).
