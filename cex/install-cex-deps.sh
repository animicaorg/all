#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${ROOT:-$HOME/animica/cex}"
ANIMICA_ROOT="${ANIMICA_ROOT:-$(cd "$ROOT/.." && pwd)}"

echo "[INFO] Installing system + Node dependencies for Animica CEX"
cd "$ROOT"

# =========================
# CHECK NODE / PNPM
# =========================
command -v node >/dev/null 2>&1 || {
  echo "[INFO] Installing Node.js..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
}

command -v pnpm >/dev/null 2>&1 || {
  echo "[INFO] Enabling corepack + pnpm..."
  corepack enable
  corepack prepare pnpm@latest --activate
}

# =========================
# CLEAN INSTALL
# =========================
echo "[INFO] Installing root dependencies..."
pnpm install

# =========================
# INSTALL ALL WORKSPACES
# =========================
echo "[INFO] Installing all workspace dependencies..."
pnpm -r install

# =========================
# BUILD CHECK (optional safety)
# =========================
echo "[INFO] Checking Vite frontend exists..."
if [ ! -d "$ROOT/apps/exchange-web/node_modules" ]; then
  echo "[WARN] exchange-web node_modules missing, reinstalling..."
  cd "$ROOT/apps/exchange-web"
  pnpm install
fi

echo "[OK] All dependencies installed successfully"

echo "[INFO] Installing admin web/API workspace dependencies..."
pnpm --dir "$ANIMICA_ROOT" install

echo "[INFO] Generating admin API Prisma client..."
pnpm --dir "$ANIMICA_ROOT/services/admin-api" exec prisma generate --schema ../exchange-api/prisma/schema.prisma

echo "[OK] Admin web/API dependencies installed successfully"
