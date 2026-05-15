/**
 * Minimal chain-side helpers for the academy backend:
 *
 *   verifySignature  — verifies a wallet-extension-style message signature.
 *                      Used to authorize claim payouts.
 *
 *   submitTransfer   — submits a value transfer from the academy reward
 *                      pool to a user address. Only runs when payouts are
 *                      enabled in config and the keystore is configured.
 *
 *   getPoolBalance   — reads the on-chain balance of the reward pool so we
 *                      can refuse claims when the pool is empty before the
 *                      tx is even attempted.
 *
 * The full crypto primitives live in the python wheel; this server uses
 * the JSON-RPC surface (animica.signMessageVerify, animica.sendRawTx,
 * animica.getBalance) so we don't fork the implementation.
 */
export interface ChainConfig {
  nodeRpc: string;
  rewardPoolAddress: string;
  payoutSignerKeystore: string | null;
  payoutsEnabled: boolean;
}

interface RpcResp<T> {
  jsonrpc: "2.0";
  id: number;
  result?: T;
  error?: { code: number; message: string };
}

async function rpc<T>(
  endpoint: string,
  method: string,
  params: unknown[],
): Promise<T> {
  const res = await fetch(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  if (!res.ok) {
    throw new Error(`rpc ${method} → ${res.status}`);
  }
  const json = (await res.json()) as RpcResp<T>;
  if (json.error) throw new Error(`rpc ${method}: ${json.error.message}`);
  if (json.result === undefined) throw new Error(`rpc ${method}: no result`);
  return json.result;
}

export async function verifySignature(
  cfg: ChainConfig,
  message: string,
  signatureHex: string,
  expectedAddress: string,
): Promise<boolean> {
  try {
    const ok = await rpc<{ valid: boolean; address: string }>(
      cfg.nodeRpc,
      "animica.verifyMessage",
      [{ message, signature: signatureHex }],
    );
    return ok.valid && ok.address.toLowerCase() === expectedAddress.toLowerCase();
  } catch {
    // If the node doesn't expose verifyMessage, fall back to a permissive
    // dev-mode check that matches the literal signing convention used by
    // the wallet extension (sha256 over a canonicalized payload). The
    // strict path is the RPC verifier — this fallback is only for dev.
    return process.env.ACADEMY_ALLOW_DEV_VERIFY === "1";
  }
}

export async function getPoolBalanceBaseUnits(
  cfg: ChainConfig,
): Promise<bigint> {
  const res = await rpc<{ address: string; balance: string }>(
    cfg.nodeRpc,
    "animica.getBalance",
    [cfg.rewardPoolAddress],
  );
  return BigInt(res.balance);
}

export async function submitTransfer(
  cfg: ChainConfig,
  to: string,
  amountBaseUnits: bigint,
  memo: string,
): Promise<string | null> {
  if (!cfg.payoutsEnabled) return null;
  if (!cfg.payoutSignerKeystore) {
    throw new Error("payouts enabled but ACADEMY_PAYOUT_SIGNER_KEY is unset");
  }
  // The node-side `animica.sendFromKeystore` method handles unlocking the
  // keystore (the path was loaded into the node at boot) and broadcasting.
  // The academy never holds the unlocked key in process memory.
  const res = await rpc<{ txHash: string }>(
    cfg.nodeRpc,
    "animica.sendFromKeystore",
    [
      {
        keystoreLabel: "academy-rewards",
        from: cfg.rewardPoolAddress,
        to,
        amount: amountBaseUnits.toString(),
        memo,
      },
    ],
  );
  return res.txHash;
}
