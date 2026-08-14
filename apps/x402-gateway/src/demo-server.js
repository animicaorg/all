#!/usr/bin/env node
'use strict';
/**
 * Demo for the x402 scaffold. Two routes:
 *
 *   GET /free/ping   — free, proves the server is up
 *   GET /paid/echo   — x402-gated at $0.005; echoes method + query back
 *
 * Run:   ANM_X402_ENABLED=1 node src/demo-server.js
 *
 * With no further env it offers whichever lanes are configured; for a fully
 * local loop also start the facilitator (ANM_X402_ENABLED=1 node
 * src/facilitator.js) and set WANM_MINT / WANM_TREASURY / WANM_USD_PRICE /
 * WANM_FEEPAYER_PUBKEY / SOLANA_RPC_URL. See README.md for the full table.
 *
 * This file binds 127.0.0.1 only. It is a demo, not a deployment.
 */

const http = require('node:http');

const { load } = require('./config');
const { createX402Gate } = require('./middleware');

function main() {
  if (process.env.ANM_X402_ENABLED !== '1') {
    console.error('refusing to start: set ANM_X402_ENABLED=1 (this scaffold is off by default)');
    process.exit(1);
  }
  const cfg = load();
  const x402 = createX402Gate();

  const paidEcho = x402.gate(
    { path: '/paid/echo', priceUsd: '0.005', description: 'Echo service (demo)', mimeType: 'application/json' },
    async (req) => {
      const url = new URL(req.url, 'http://localhost');
      return {
        status: 200,
        body: JSON.stringify({
          echo: { method: req.method, path: url.pathname, query: Object.fromEntries(url.searchParams) },
          paidWith: 'x402',
        }),
      };
    }
  );

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    try {
      if (url.pathname === '/free/ping') {
        const body = JSON.stringify({ ok: true, service: 'x402-demo', time: new Date().toISOString() });
        res.writeHead(200, { 'content-type': 'application/json' });
        return res.end(body);
      }
      if (url.pathname === '/paid/echo') {
        return await paidEcho(req, res);
      }
      res.writeHead(404, { 'content-type': 'application/json' });
      return res.end(JSON.stringify({ error: 'not_found', routes: ['/free/ping', '/paid/echo'] }));
    } catch (e) {
      console.error('demo request failed', e);
      if (!res.headersSent) res.writeHead(500, { 'content-type': 'application/json' });
      return res.end(JSON.stringify({ error: 'internal_error' }));
    }
  });

  server.listen(cfg.demoPort, '127.0.0.1', () => {
    console.log(`x402 demo on http://127.0.0.1:${cfg.demoPort}  (free: /free/ping, gated: /paid/echo @ $0.005)`);
    const lanes = x402.buildAccepts({ path: '/paid/echo', priceUsd: '0.005' });
    if (lanes.length === 0) {
      console.warn('no payment lanes configured — /paid/echo will answer 503. See README env table.');
    } else {
      for (const l of lanes) console.log(`  lane: ${l.scheme} ${l.network} amount=${l.amount} -> ${l.payTo}`);
    }
  });
}

main();
