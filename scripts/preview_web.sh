#!/usr/bin/env bash
set -Eeuo pipefail

declare -A apps=(
  [website]=3000
  [explorer-web]=3001
  [studio-web]=3002
  [wallet]=3003
)

link_sdk() {
  if [ -d sdk/dist ]; then
    for app in "${!apps[@]}"; do
      [ -d "$app" ] || continue
      if grep -q '"@animica/sdk"' "$app/package.json" 2>/dev/null; then
        ( cd "$app" && npm i @animica/sdk@file:../sdk >/dev/null )
      fi
    done
  fi
}

run_one () {
  local dir="$1" port="$2"
  [ -d "$dir" ] || { echo "[skip] $dir (missing)"; return; }
  echo "[start] $dir on :$port"
  cd "$dir"
  [ -d node_modules ] || npm i

  # crude framework sniffing
  if grep -q '"next"' package.json 2>/dev/null; then
    HOSTNAME=0.0.0.0 PORT="$port" nohup npm run dev -- -p "$port" > "../logs/${dir}.log" 2>&1 &
  elif grep -q '"vite"' package.json 2>/dev/null; then
    HOST=0.0.0.0 PORT="$port" nohup npm run dev -- --port "$port" --host 0.0.0.0 > "../logs/${dir}.log" 2>&1 &
  elif grep -q '"react-scripts"' package.json 2>/dev/null; then
    HOST=0.0.0.0 PORT="$port" nohup npm start > "../logs/${dir}.log" 2>&1 &
  else
    HOST=0.0.0.0 PORT="$port" nohup npm run dev -- --port "$port" --host 0.0.0.0 > "../logs/${dir}.log" 2>&1 &
  fi
  cd - >/dev/null
}

link_sdk
for d in "${!apps[@]}"; do run_one "$d" "${apps[$d]}"; done
echo "[ok] launched. Tail logs with: tail -f logs/*.log"
