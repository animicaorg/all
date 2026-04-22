FROM node:20-bullseye

WORKDIR /app

COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY services/usdan-api/package.json services/usdan-api/package.json
RUN corepack enable && pnpm install --filter @animica/usdan-api... --frozen-lockfile=false

COPY services/usdan-api ./services/usdan-api
WORKDIR /app/services/usdan-api
RUN pnpm build

CMD ["pnpm", "start"]
