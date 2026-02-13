#!/usr/bin/env node
import fs from 'node:fs';

const rpcUrl = process.env.ANIMICA_RPC_URL || 'https://mainnet.animica.org/rpc';
const fixturePath = new URL('../tests/fixtures/tx-golden-v2-transfer.rawtx.hex', import.meta.url);
const rawTx = fs.readFileSync(fixturePath, 'utf8').trim();

const body = {
  jsonrpc: '2.0',
  id: Date.now(),
  method: 'tx.sendRawTransaction',
  params: [rawTx],
};

console.log('[smoke] request body:', JSON.stringify(body));

const res = await fetch(rpcUrl, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
});

const json = await res.json();
console.log('[smoke] raw response:', JSON.stringify(json));

if (json?.error?.code === -32602) {
  throw new Error('RPC rejected tx.sendRawTransaction params with -32602; request shape is invalid');
}

console.log('[smoke] PASS: request shape accepted by RPC parameter validation');
