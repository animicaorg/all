#!/usr/bin/env node
/**
 * Dev-time reverse proxy for Animica Studio Web:
 * - Proxies JSON-RPC and WS to the node
 * - Proxies /services/* to studio-services (deploy/verify/faucet/simulate)
 * - Adds strict but configurable CORS for the Vite dev origin
 *
 * Usage:
 *   node scripts/dev_proxy.mjs
 *
 * Env (with sane defaults):
 *   DEV_PROXY_PORT=5050
 *   DEV_PROXY_RPC=http://127.0.0.1:8545
 *   DEV_PROXY_SERVICES=http://127.0.0.1:8081
 *   DEV_PROXY_ALLOW_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
 *
 * Notes:
 *   - Requires the "http-proxy" package as a dev dependency:
 *       pnpm add -D http-proxy
 *       # or: npm i -D http-proxy / yarn add -D http-proxy
 */

import http from 'node:http';
import { URL } from 'node:url';
import createProxyServer from 'http-proxy';

const PORT = parseInt(process.env.DEV_PROXY_PORT || '5050', 10);
const RPC_URL = process.env.DEV_PROXY_RPC || process.env.VITE_RPC_URL || 'http://127.0.0.1:8545';
const SERVICES_URL =
  process.env.DEV_PROXY_SERVICES || process.env.VITE_SERVICES_URL || 'http://127.0.0.1:8081';
const ALLOW_ORIGINS = (process.env.DEV_PROXY_ALLOW_ORIGINS ||
  'http://localhost:5173,http://127.0.0.1:5173')
  .split(',')
  .map(s => s.trim())
  .filter(Boolean);

const proxy = createProxyServer({
  changeOrigin: true,
  xfwd: true,
  ws: true,
  secure: false, // dev environments often use self-signed certs
});

proxy.on('error', (err, req, res) => {
  if (!res.headersSent) {
    res.writeHead(502, { 'content-type': 'application/json; charset=utf-8' });
  }
  res.end(JSON.stringify({ error: 'Bad gateway', detail: String(err?.message || err) }));
});

function allowedOrigin(origin) {
  return (
    !origin ||
    ALLOW_ORIGINS.includes('*') ||
    ALLOW_ORIGINS.some(o => o.toLowerCase() === String(origin).toLowerCase())
  );
}

function setCors(res, origin) {
  if (origin && allowedOrigin(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
    res.setHeader('Vary', 'Origin');
  }
  res.setHeader('Access-Control-Allow-Credentials', 'true');
  res.setHeader(
    'Access-Control-Allow-Headers',
    'authorization,content-type,x-requested-with,x-csrf-token'
  );
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS');
}

function notFound(res, msg = 'Not found') {
  res.writeHead(404, { 'content-type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify({ error: msg }));
}

const server = http.createServer((req, res) => {
  const origin = req.headers.origin || '';
  setCors(res, origin);

  if (req.method === 'OPTIONS') {
    // CORS preflight
    res.writeHead(204);
    return res.end();
  }

  const url = new URL(req.url || '/', 'http://127.0.0.1');

  // Proxy to node RPC (HTTP JSON-RPC)
  if (url.pathname === '/rpc' || url.pathname === '/openrpc.json') {
    return proxy.web(req, res, { target: RPC_URL });
  }

  // Proxy to node WS hub (/ws)
  if (url.pathname === '/ws' || url.pathname.startsWith('/ws/')) {
    return proxy.web(req, res, { target: RPC_URL });
  }

  // Proxy to studio-services, strip leading /services
  if (url.pathname === '/services' || url.pathname.startsWith('/services/')) {
    req.url = url.pathname.replace(/^\/services/, '') + url.search;
    return proxy.web(req, res, { target: SERVICES_URL });
  }

  // Helpful index
  if (url.pathname === '/' || url.pathname === '/__health') {
    res.writeHead(200, { 'content-type': 'application/json; charset=utf-8' });
    return res.end(
      JSON.stringify(
        {
          status: 'ok',
          rpc: RPC_URL,
          services: SERVICES_URL,
          allow_origins: ALLOW_ORIGINS,
          routes: {
            rpc: '/rpc',
            ws: '/ws',
            services_prefix: '/services/*',
            openrpc: '/openrpc.json',
          },
        },
        null,
        2
      )
    );
  }

  return notFound(res);
});

// WebSocket upgrade handling
server.on('upgrade', (req, socket, head) => {
  try {
    const url = new URL(req.url || '/', 'http://127.0.0.1');
    if (url.pathname === '/ws' || url.pathname.startsWith('/ws/')) {
      // Proxy WS to the node RPC
      proxy.ws(req, socket, head, { target: RPC_URL });
    } else if (url.pathname.startsWith('/services/')) {
      // Strip /services and proxy WS to studio-services if needed
      req.url = url.pathname.replace(/^\/services/, '') + url.search;
      proxy.ws(req, socket, head, { target: SERVICES_URL });
    } else {
      socket.write('HTTP/1.1 404 Not Found\r\n\r\n');
      socket.destroy();
    }
  } catch (err) {
    socket.write('HTTP/1.1 400 Bad Request\r\n\r\n');
    socket.destroy();
  }
});

server.listen(PORT, () => {
  const banner = [
    'Animica Studio Dev Proxy',
    '────────────────────────',
    `Listening on:  http://127.0.0.1:${PORT}`,
    `RPC target:    ${RPC_URL}`,
    `Services:      ${SERVICES_URL}`,
    `Allowed CORS:  ${ALLOW_ORIGINS.join(', ') || '(none)'}`,
    '',
    'Routes:',
    `  POST  /rpc              → ${RPC_URL}/rpc`,
    `  GET   /openrpc.json     → ${RPC_URL}/openrpc.json`,
    `  WS    /ws               → ${RPC_URL}/ws`,
    `  *     /services/*       → ${SERVICES_URL}/*`,
    '',
  ].join('\n');
  console.log(banner);
});
