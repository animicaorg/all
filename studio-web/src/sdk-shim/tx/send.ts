import { JsonRpcHttpClient } from "../rpc/http";

export type SendArgs = { tx?: any; signBytes: Uint8Array; signature: Uint8Array };

export async function sendSigned(args: SendArgs): Promise<{ txHash: string }> {
  // naive hash for determinism
  const hash = "0x" + Array.from(args.signature)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 64)
    .padEnd(64, "0");
  return { txHash: hash };
}

export async function sendSignedTx(args: SendArgs) {
  return sendSigned(args);
}

export async function awaitReceipt(_txHash?: string | null): Promise<any> {
  return null;
}

export type RpcClient = JsonRpcHttpClient;
