# ------------------------------------------------------------------------------
# Animica Explorer Web — Static Site Image
# - Builds explorer-web (Vite/React) with Node 20, serves via nginx:alpine
# - SPA routing with /index.html fallback
# - Correct MIME for WASM, gzip, long-cache for hashed assets
# - Security headers, healthcheck
# ------------------------------------------------------------------------------

# ===== Stage 1: build assets ===================================================
FROM node:20-alpine AS build
WORKDIR /app

# Install deps first to leverage layer cache
COPY explorer-web/package*.json explorer-web/tsconfig.json explorer-web/vite.config.ts /app/
RUN npm ci --no-audit --no-fund

# Copy source and build
COPY explorer-web/ /app/
RUN npm run build

# ===== Stage 2: serve with nginx =============================================
FROM nginx:1.25-alpine

ENV EXPLORER_WEB_PORT=8087

# Remove default site
RUN rm -f /etc/nginx/conf.d/default.conf

# Nginx server config:
# - listens on EXPLORER_WEB_PORT
# - /healthz endpoint for k8s/compose healthchecks
# - immutable cache headers for hashed assets
# - SPA fallback for client-side routing
# - minimal security headers (CSP intentionally omitted for dev RPC/WS flexibility)
RUN printf '%s\n' \
'server {' \
'  listen       '"${EXPLORER_WEB_PORT}"';' \
'  server_name  _;' \
'  root         /usr/share/nginx/html;' \
'  index        index.html;' \
'' \
'  # Health endpoint' \
'  location = /healthz {' \
'    add_header Content-Type text/plain always;' \
'    return 200 "ok\n";' \
'  }' \
'' \
'  # Long cache for hashed assets (Vite emits content-hashed filenames)' \
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
'  # SPA fallback' \
'  location / {' \
'    try_files $uri $uri/ /index.html;' \
'  }' \
'' \
'  # Extra MIME (WASM)' \
'  types { application/wasm wasm; }' \
'' \
'  # Security headers' \
'  add_header X-Content-Type-Options "nosniff" always;' \
'  add_header X-Frame-Options "DENY" always;' \
'  add_header Referrer-Policy "strict-origin-when-cross-origin" always;' \
'  add_header Cross-Origin-Opener-Policy "same-origin" always;' \
'  add_header Cross-Origin-Resource-Policy "same-origin" always;' \
'}' \
> /etc/nginx/conf.d/explorer-web.conf

# Gzip
RUN printf '%s\n' \
'gzip on;' \
'gzip_comp_level 6;' \
'gzip_min_length 1024;' \
'gzip_types text/plain text/css application/json application/javascript application/wasm application/xml image/svg+xml;' \
> /etc/nginx/conf.d/gzip.conf

# Copy built assets
COPY --from=build /app/dist /usr/share/nginx/html

# nginx image already runs as non-root user `nginx`
EXPOSE 8087

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=5 \
  CMD wget -q -O /dev/null http://127.0.0.1:8087/healthz || exit 1

# OCI labels
ARG VERSION=0.0.0+local
ARG VCS_REF=unknown
ARG BUILD_DATE
LABEL org.opencontainers.image.title="animica-explorer-web" \
      org.opencontainers.image.description="Static build of Animica Explorer Web served by nginx" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="Apache-2.0"

# Use default nginx foreground CMD
