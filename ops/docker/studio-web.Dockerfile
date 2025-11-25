# ------------------------------------------------------------------------------
# Animica Studio Web — Static Site Image
# - Builds studio-web (Vite/React) with Node, serves via nginx:alpine
# - SPA routing (fallback to /index.html)
# - Correct MIME for WASM, gzip, long-cache for hashed assets
# - Security headers, healthcheck, non-root nginx user
# ------------------------------------------------------------------------------

# ===== Stage 1: build assets ===================================================
FROM node:20-alpine AS build
WORKDIR /app

# Install deps with layer cache
COPY studio-web/package*.json studio-web/tsconfig.json studio-web/vite.config.ts /app/
# Optional configs if present (ignore if missing)
# COPY studio-web/.npmrc /app/.npmrc

RUN npm ci --no-audit --no-fund

# Copy rest and build
COPY studio-web/ /app/
RUN npm run build

# ===== Stage 2: serve with nginx =============================================
FROM nginx:1.25-alpine

ENV STUDIO_PORT=8088

# Clean default site
RUN rm -f /etc/nginx/conf.d/default.conf

# Nginx config: SPA routing, cache headers, gzip, MIME for WASM, healthz
RUN printf '%s\n' \
'server {' \
'  listen       '"${STUDIO_PORT}"';' \
'  server_name  _;' \
'  root         /usr/share/nginx/html;' \
'  index        index.html;' \
'' \
'  # Health endpoint used by container healthcheck' \
'  location = /healthz {' \
'    add_header Content-Type text/plain always;' \
'    return 200 "ok\n";' \
'  }' \
'' \
'  # Long cache for hashed assets (Vite outputs hashed filenames)' \
'  location ~* \.(?:js|mjs|css|map|wasm|woff2|svg|png|jpg|jpeg|gif|ico)$ {' \
'    add_header Access-Control-Allow-Origin * always;' \
'    add_header Cache-Control "public, max-age=31536000, immutable" always;' \
'    try_files $uri =404;' \
'  }' \
'' \
'  # Do not cache the HTML entrypoint' \
'  location = /index.html {' \
'    add_header Cache-Control "no-store" always;' \
'  }' \
'' \
'  # SPA fallback for client-side routes' \
'  location / {' \
'    try_files $uri $uri/ /index.html;' \
'  }' \
'' \
'  # Extra MIME (WASM may not be present in some builds)' \
'  types { application/wasm wasm; }' \
'' \
'  # Security headers (CSP omitted to avoid blocking RPC/WS during dev)' \
'  add_header X-Content-Type-Options "nosniff" always;' \
'  add_header X-Frame-Options "DENY" always;' \
'  add_header Referrer-Policy "strict-origin-when-cross-origin" always;' \
'  add_header Cross-Origin-Opener-Policy "same-origin" always;' \
'  add_header Cross-Origin-Resource-Policy "same-origin" always;' \
'}' \
> /etc/nginx/conf.d/studio-web.conf

# Gzip (safe defaults)
RUN printf '%s\n' \
'gzip on;' \
'gzip_comp_level 6;' \
'gzip_min_length 1024;' \
'gzip_types text/plain text/css application/json application/javascript application/wasm application/xml image/svg+xml;' \
> /etc/nginx/conf.d/gzip.conf

# Copy built assets
COPY --from=build /app/dist /usr/share/nginx/html

# Non-root already provided by nginx image (user: nginx)
EXPOSE 8088

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=5 \
  CMD wget -q -O /dev/null http://127.0.0.1:8088/healthz || exit 1

# OCI labels
ARG VERSION=0.0.0+local
ARG VCS_REF=unknown
ARG BUILD_DATE
LABEL org.opencontainers.image.title="animica-studio-web" \
      org.opencontainers.image.description="Static build of Animica Studio Web served by nginx" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="Apache-2.0"

# Default CMD from nginx base: run nginx in foreground
