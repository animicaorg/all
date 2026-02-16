#!/usr/bin/env node
import process from 'node:process';

const rpcUrl = process.env.RPC_URL || 'https://mainnet.animica.org/rpc';
const rawTx = process.argv[2] || process.env.RAW_TX;

if (!rawTx) {
  console.error('Usage: node scripts/test-sendrawtx.mjs <rawTxHex>');
  process.exit(1);
}

function normalizeRawTx(input) {
  const trimmed = String(input).trim();
  if (!trimmed.startsWith('0x') && !trimmed.startsWith('0X')) {
    throw new Error('CLIENT_INVALID_RAWTX: missing 0x prefix');
  }
  const body = trimmed.slice(2);
  if (!/^[0-9a-f]*$/i.test(body)) throw new Error('CLIENT_INVALID_RAWTX: not hex');
  if (body.length < 4) throw new Error('CLIENT_INVALID_RAWTX: payload too short');
  if (body.length % 2 !== 0) throw new Error('CLIENT_INVALID_RAWTX: hex length must be even');
  return `0x${body.toLowerCase()}`;
}

let reqId = 1;
const nextId = () => reqId++;

async function rpcCall(method, params) {
  const payload = { jsonrpc: '2.0', id: nextId(), method, params };
  const res = await fetch(rpcUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const text = await res.text();
  let json = {};
  try { json = text ? JSON.parse(text) : {}; } catch {}
  return { status: res.status, json, payload };
}

function extractHash(result) {
  if (typeof result === 'string' && result.startsWith('0x')) return result;
  if (!result || typeof result !== 'object') return null;
  for (const key of ['txHash', 'transactionHash', 'hash', 'txid']) {
    if (typeof result[key] === 'string' && result[key].startsWith('0x')) return result[key];
  }
  return null;
}

async function main() {
  const normalized = normalizeRawTx(rawTx);
  const methodsOut = await rpcCall('rpc.listMethods', []);
  const listed = Array.isArray(methodsOut.json?.result) ? methodsOut.json.result : [];

  const base = [
    ['tx.sendRawTransaction', 'objectArray'],
    ['tx_sendRawTransaction', 'objectArray'],
    ['tx.submitRawTransaction', 'objectArray'],
    ['tx2.sendRawTransaction', 'objectArray'],
    ['tx.sendRawTransaction', 'array'],
    ['tx_sendRawTransaction', 'array'],
    ['tx.submitRawTransaction', 'array'],
    ['tx2.sendRawTransaction', 'array'],
  ];

  const attempts = [];
  for (const [method, shape] of base) {
    if (method.includes('submit') || method.includes('tx2')) {
      if (!listed.includes(method)) continue;
    }
    const params = shape === 'objectArray' ? [{ rawTx: normalized }] : [normalized];
    const first = await rpcCall(method, params);
    if (first.json?.result !== undefined) {
      const txHash = extractHash(first.json.result);
      attempts.push({ method, shape, ok: true, txHash });
      console.log(JSON.stringify({ ok: true, method, shape, txHash, attempts }, null, 2));
      return;
    }

    const code = typeof first.json?.error?.code === 'number' ? first.json.error.code : 'RPC_ERROR_UNKNOWN';
    attempts.push({ method, shape, ok: false, code, message: first.json?.error?.message || 'failed' });

    if (code === -32603) {
      const second = await rpcCall(method, params);
      if (second.json?.result !== undefined) {
        const txHash = extractHash(second.json.result);
        attempts.push({ method, shape, ok: true, txHash, retry: true });
        console.log(JSON.stringify({ ok: true, method, shape, txHash, attempts }, null, 2));
        return;
      }
      attempts.push({ method, shape, ok: false, retry: true, code: second.json?.error?.code || 'RPC_ERROR_UNKNOWN', message: second.json?.error?.message || 'failed' });
    }
  }

  console.log(JSON.stringify({ ok: false, rpcUrl, attempts }, null, 2));
  process.exit(2);
}

main().catch((err) => {
  console.error(err?.stack || String(err));
  process.exit(1);
});
