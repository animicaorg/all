import { config } from './config';

// Read-only chain access + a guarded local-sign send path.
// Payment truth model (measured, not assumed): inclusion != execution. Receipts carry
// status:null; headers commit zero stateRoot. So we NEVER credit on tx presence — only on an
// observed recipient BALANCE DELTA after finality (>=12 confs). Reads hit the node directly
// (127.0.0.1:8545), never the read-through cache which can be stale.

let rpcId = 1;
async function rpc<T = any>(method: string, params: any[] = [], timeoutMs = 12_000): Promise<T> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(config.rpcUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: rpcId++, method, params }),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new Error(`rpc ${method} http ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(`rpc ${method}: ${data.error.message ?? JSON.stringify(data.error)}`);
    return data.result as T;
  } finally {
    clearTimeout(t);
  }
}

export async function getHead(): Promise<{ height: number; hash: string }> {
  const r = await rpc<any>('chain.getHead');
  return { height: Number(r.height), hash: String(r.hash) };
}

// Confirmed balance in nANM plus the head height it was read at (for delta accounting).
export async function getAddressBalance(address: string): Promise<{ nanm: bigint; asOfHeight: number }> {
  const r = await rpc<any>('state.getAddressBalance', [address]);
  // { confirmed_balance, unit:'nANM', display_decimals, as_of_head_height, as_of_head_hash }
  const raw = r.confirmed_balance ?? r.balance ?? '0';
  const nanm = typeof raw === 'string' && raw.startsWith('0x') ? BigInt(raw) : BigInt(raw);
  return { nanm, asOfHeight: Number(r.as_of_head_height ?? 0) };
}

export interface TxStatus {
  state: string; // pending_mempool | included_block | finalized | rejected | evicted | reorged_out
  confirmations: number;
  finalized: boolean;
  blockNumber: number | null;
}

export async function getTxStatus(txid: string): Promise<TxStatus | null> {
  try {
    const r = await rpc<any>('tx.getStatus', [txid]);
    if (!r) return null;
    return {
      state: String(r.state ?? r.status ?? 'unknown'),
      confirmations: Number(r.confirmations ?? 0),
      finalized: Boolean(r.finalized),
      blockNumber: r.blockNumber != null ? Number(r.blockNumber) : (r.block_number != null ? Number(r.block_number) : null),
    };
  } catch {
    return null;
  }
}

export async function getTransaction(txid: string): Promise<any | null> {
  // tx.getTransactionByHash is the real method; tx.get is the worker-proven alias.
  for (const m of ['tx.getTransactionByHash', 'tx.get']) {
    try {
      const r = await rpc<any>(m, [txid]);
      if (r) return r;
    } catch { /* try next */ }
  }
  return null;
}

export async function getReceipt(txid: string): Promise<any | null> {
  try {
    return await rpc<any>('tx.getReceipt', [txid]);
  } catch {
    return null;
  }
}
