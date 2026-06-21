#!/usr/bin/env bash
# Publish the Animica MCP server to the OFFICIAL MCP registry
# (registry.modelcontextprotocol.io) — the biggest agent-discovery channel.
#
# Run this on your machine (it needs ONE browser approval for GitHub login,
# which an automation can't do for you). ~2 minutes.
#
#   bash animica-mcp/publish-to-registry.sh
set -euo pipefail
cd "$(dirname "$0")"   # the animica-mcp/ dir, where server.json lives

# --- 0. namespace must match the GitHub identity you log in as --------------
# server.json currently uses: io.github.animicaorg/animica
# • Log in as the 'animicaorg' org owner            -> keep it.
# • Log in as your personal account (e.g. ercmine)  -> set NS=io.github.ercmine/animica
NS="${NS:-}"
if [ -n "$NS" ]; then
  tmp="$(mktemp)"; sed "s#\"name\": \"io.github.[^\"]*\"#\"name\": \"$NS\"#" server.json > "$tmp" && mv "$tmp" server.json
  echo ">> server.json name set to: $NS"
fi

# --- 1. get the publisher CLI ----------------------------------------------
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"; ARCH="$(uname -m)"
case "$ARCH" in x86_64|amd64) ARCH=amd64;; arm64|aarch64) ARCH=arm64;; esac
BIN=./mcp-publisher
if [ ! -x "$BIN" ]; then
  echo ">> downloading mcp-publisher ($OS/$ARCH)…"
  curl -fsSL "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_${OS}_${ARCH}.tar.gz" | tar xz mcp-publisher
  chmod +x "$BIN"
fi

# --- 2. log in (browser approval) ------------------------------------------
echo ">> logging in to the MCP registry via GitHub (approve in your browser)…"
"$BIN" login github

# --- 3. publish -------------------------------------------------------------
echo ">> publishing server.json…"
"$BIN" publish
echo ">> Done. Verify: https://registry.modelcontextprotocol.io/v0/servers?search=animica"
echo ">> Agents using Cursor/Claude/Windsurf can now discover Animica in-registry."
