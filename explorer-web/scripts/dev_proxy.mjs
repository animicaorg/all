import 'dotenv/config';
import http from 'http';
import { createProxyServer } from 'http-proxy';

const PORT = Number(process.env.DEV_PROXY_PORT || 5174);

const RPC_HTTP = process.env.RPC_URL || process.env.VITE_RPC_URL || 'http://127.0.0.1:8545';
const RPC_WS =
  process.env.RPC_WS_URL ||
  (RPC_HTTP.startsWith('https://')
    ? RPC_HTTP.replace('https://', 'wss://')
    : RPC_HTTP.replace('http://', 'ws://'));
const SERVICES = process.env.SERVICES_URL || process.env.VITE_SERVICES_URL || 'http://127.0.0.1:8000';

const ALLOW_ORIGINS = (process.env.ALLOW_ORIGINS || '').split(',').map(s => s.trim()).filter(Boolean);
const ALLOW_ALL = ALLOW_ORIGINS.length === 0; // default: allow *

const proxy = createProxyServer({
  secure: false,
  changeOrigin: true,
  ws: true,
  ignorePath: true,
});

function allowOrigin(originHeader) {
  if (ALLOW_ALL) return '*';
  const origin = originHeader || '';
  return ALLOW_ORIGINS.includes(origin) ? origin : ALLOW_ORIGINS[0] || 'http://localhost:5173';
}

function setCors(req, res) {
  const origin = allowOrigin(req.headers.origin);
  res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Vary', 'Origin');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,PATCH,DELETE,OPTIONS');
  res.setHeader(
    'Access-Control-Allow-Headers',
    'Content-Type, Authorization, X-Requested-With, X-API-Key, X-Client-Id'
  );
  res.setHeader('Access-Control-Max-Age', '86400');
}

function log(...args) {
  const now = new Date().toISOString();
  console.log(now, '-', ...args);
}

proxy.on('proxyReq', (proxyReq, req) => {
  // Ensure JSON content-type for RPC POSTs if not provided
  if (req.url === '/' && req.method === 'POST' && !req.headers['content-type']) {
    proxyReq.setHeader('content-type', 'application/json');
  }
});

proxy.on('proxyRes', (proxyRes, req, res) => {
  // CORS on proxied responses
  setCors(req, res);
  log(`[${proxyRes.statusCode}] ${req.method} ${req.url}`);
});

proxy.on('error', (err, req, res) => {
  const msg = `Proxy error: ${err.code || ''} ${err.message || err}`;
  if (!res.headersSent) {
    res.writeHead(502, { 'Content-Type': 'application/json' });
  }
  res.end(JSON.stringify({ error: 'Bad Gateway', detail: msg }));
  log(msg);
});

const server = http.createServer((req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);

    // CORS preflight
    if (req.method === 'OPTIONS') {
      setCors(req, res);
      res.writeHead(204);
      return res.end();
    }

    // Health check
    if (url.pathname === '/healthz') {
      setCors(req, res);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ ok: true, rpc: RPC_HTTP, services: SERVICES }));
    }

    // Route: /rpc -> RPC root
    if (url.pathname === '/rpc' || url.pathname.startsWith('/rpc/')) {
      // Forward all JSON-RPC HTTP requests to RPC root "/"
      const forwardPath = '/';
      req.url = forwardPath + (url.search || '');
      setCors(req, res);
      return proxy.web(req, res, { target: RPC_HTTP });
    }

    // Route: /services -> studio-services (strip prefix)
    if (url.pathname === '/services' || url.pathname.startsWith('/services/')) {
      const stripped = url.pathname.replace(/^\/services/, '') || '/';
      req.url = stripped + (url.search || '');
      setCors(req, res);
      return proxy.web(req, res, { target: SERVICES });
    }

    // Not found
    setCors(req, res);
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Not Found', path: url.pathname }));
  } catch (e) {
    res.writeHead(500, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Internal Error', detail: String(e) }));
  }
});

// WebSocket upgrades: /ws -> RPC WS
server.on('upgrade', (req, socket, head) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);
    if (url.pathname === '/ws' || url.pathname.startsWith('/ws/')) {
      req.url = '/'; // most RPC WS servers expect root
      proxy.ws(req, socket, head, { target: RPC_WS, secure: false, changeOrigin: true });
      log(`[WS] -> ${RPC_WS}`);
    } else if (url.pathname.startsWith('/services')) {
      // If your services expose websockets, forward similarly (optional)
      req.url = url.pathname.replace(/^\/services/, '') || '/';
      proxy.ws(req, socket, head, { target: SERVICES, secure: false, changeOrigin: true });
      log(`[WS] -> ${SERVICES}${req.url}`);
    } else {
      socket.destroy();
    }
  } catch {
    socket.destroy();
  }
});

server.listen(PORT, () => {
  const banner = `
┌─────────────────────────────────────────────────────────┐
│ explorer-web dev proxy                                  │
├─────────────────────────────────────────────────────────┤
│ HTTP  -> http://127.0.0.1:${PORT}/rpc       → ${RPC_HTTP}
│ WS    -> ws://127.0.0.1:${PORT}/ws          → ${RPC_WS}
│ API   -> http://127.0.0.1:${PORT}/services  → ${SERVICES}
│ Health: http://127.0.0.1:${PORT}/healthz
└─────────────────────────────────────────────────────────┘
Allowed origins: ${ALLOW_ALL ? '*' : ALLOW_ORIGINS.join(', ')}
`;
  console.log(banner);
});

process.on('SIGINT', () => {
  console.log('\nShutting down dev proxy...');
  server.close(() => process.exit(0));
});
