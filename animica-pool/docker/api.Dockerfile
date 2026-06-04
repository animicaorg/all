# Animica Pool API (NestJS)
FROM node:20-slim AS base
RUN corepack enable && apt-get update && apt-get install -y openssl && rm -rf /var/lib/apt/lists/*
WORKDIR /app

COPY pnpm-workspace.yaml package.json ./
COPY prisma ./prisma
COPY packages/shared/package.json ./packages/shared/
COPY apps/api/package.json ./apps/api/
RUN pnpm install --frozen-lockfile=false

COPY packages/shared ./packages/shared
COPY apps/api ./apps/api
RUN pnpm prisma:generate && pnpm --filter @animica/api build

EXPOSE 4000
CMD ["node", "apps/api/dist/main.js"]
