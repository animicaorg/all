// On-chain ANM payout via the Animica node JSON-RPC (wallet.send), mirroring
// the buy.animica.org gateway. Gated: requires ANIMICA_RPC_URL +
// ANIMICA_PAYOUT_FROM_ADDRESS. Never logs keys.
import { env } from "../config/env";

export function anmPayoutsEnabled(): boolean {
  const e = env();
  return !!e.ANIMICA_RPC_URL && !!e.ANIMICA_PAYOUT_FROM_ADDRESS;
}

async function rpc<T>(method: string, params: unknown): Promise<T> {
  const e = env();
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (e.ANIMICA_RPC_USER) {
    headers.authorization = "Basic " + Buffer.from(`${e.ANIMICA_RPC_USER}:${e.ANIMICA_RPC_PASSWORD}`).toString("base64");
  }
  const res = await fetch(e.ANIMICA_RPC_URL, {
    method: "POST",
    headers,
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  if (!res.ok) throw new Error(`Animica RPC ${method} → ${res.status}`);
  const json: any = await res.json();
  if (json.error) throw new Error(`Animica RPC ${method}: ${JSON.stringify(json.error)}`);
  return json.result as T;
}

/** Send ANM on-chain. Returns the txid. amountAnm is a decimal string. */
export async function sendAnm(toAddress: string, amountAnm: string): Promise<string> {
  const e = env();
  const result = await rpc<{ txid?: string; hash?: string } | string>("wallet.send", {
    from: e.ANIMICA_PAYOUT_FROM_ADDRESS,
    to: toAddress,
    amount: amountAnm,
    unit: "anm",
  });
  if (typeof result === "string") return result;
  return result.txid ?? result.hash ?? "";
}
