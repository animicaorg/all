# Animica Pool Web (Next.js)
FROM node:20-slim AS base
RUN corepack enable
WORKDIR /app

COPY pnpm-workspace.yaml package.json ./
COPY packages/shared/package.json ./packages/shared/
COPY apps/web/package.json ./apps/web/
RUN pnpm install --frozen-lockfile=false

COPY packages/shared ./packages/shared
COPY apps/web ./apps/web
RUN pnpm --filter @animica/web build

EXPOSE 3000
CMD ["pnpm", "--filter", "@animica/web", "start"]
