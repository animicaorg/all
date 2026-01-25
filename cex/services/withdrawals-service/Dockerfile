# Stage 1: Builder
FROM node:20-alpine AS builder

WORKDIR /app

# Enable pnpm
RUN corepack enable

# Copy workspace files
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages ./packages
COPY services/withdrawals-service/package.json ./services/withdrawals-service/

# Install dependencies
RUN pnpm install --frozen-lockfile

# Copy source
COPY services/withdrawals-service ./services/withdrawals-service
COPY tsconfig.base.json ./

# Build
RUN pnpm --filter @cex/withdrawals-service build

# Stage 2: Runtime
FROM node:20-alpine AS runtime

# Security: Run as non-root user
RUN addgroup -g 1001 -S nodejs && \
    adduser -S nodejs -u 1001

WORKDIR /app

# Enable pnpm
RUN corepack enable

# Copy package files
COPY --from=builder /app/package.json /app/pnpm-lock.yaml /app/pnpm-workspace.yaml ./
COPY --from=builder /app/packages ./packages
COPY --from=builder /app/services/withdrawals-service/package.json ./services/withdrawals-service/

# Install production dependencies only
RUN pnpm install --frozen-lockfile --prod

# Copy built artifacts
COPY --from=builder /app/services/withdrawals-service/dist ./services/withdrawals-service/dist

# Change ownership
RUN chown -R nodejs:nodejs /app

USER nodejs

EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD node -e "require('http').get('http://localhost:3000/health', (r) => {process.exit(r.statusCode === 200 ? 0 : 1)})"

# Start service
CMD ["node", "services/withdrawals-service/dist/index.js"]
