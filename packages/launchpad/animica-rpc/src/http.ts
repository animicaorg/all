import { NETWORK } from "@launchpad/shared";
import type { AnimicaNetworkInfo, AnimicaTx } from "@launchpad/shared";
import { AnimicaRpcClient, AnimicaRpcError } from "./types";

interface HttpRpcOptions {
  rpcUrl?: string;
  chainId?: number;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
}

interface JsonRpcResponse<T = unknown> {
  jsonrpc?: string;
  id: number | string;
  result?: T;
  error?: { code: number | string; message: string };
}

export class HttpAnimicaRpc implements AnimicaRpcClient {
  private url: string;
  private chainId: number;
  private fetchImpl: typeof fetch;
  private timeoutMs: number;

  constructor(opts: HttpRpcOptions = {}) {
    this.url = opts.rpcUrl ?? NETWORK.defaultRpcUrl;
    this.chainId = opts.chainId ?? NETWORK.defaultChainId;
    this.fetchImpl = opts.fetchImpl ?? fetch;
    this.timeoutMs = opts.timeoutMs ?? 10_000;
  }

  async call(method: string, params: unknown[] = []): Promise<unknown> {
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const res = await this.fetchImpl(this.url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
        signal: controller.signal
      });
      if (!res.ok) {
        throw new AnimicaRpcError("HTTP_" + res.status, `Animica RPC HTTP ${res.status}`);
      }
      const data = (await res.json()) as JsonRpcResponse;
      if (data.error) {
        throw new AnimicaRpcError(String(data.error.code), data.error.message);
      }
      return data.result;
    } finally {
      clearTimeout(t);
    }
  }

  async getHeight(): Promise<number> {
    const result = (await this.call("chain.height", [])) as number | string;
    const n = typeof result === "number" ? result : Number(result);
    return Number.isFinite(n) ? n : 0;
  }

  async getNetwork(): Promise<AnimicaNetworkInfo> {
    let height: number | undefined;
    try {
      height = await this.getHeight();
    } catch {
      height = undefined;
    }
    return {
      chainId: this.chainId,
      chainName: NETWORK.chainName,
      rpcUrl: this.url,
      height
    };
  }

  async validateAddress(address: string): Promise<boolean> {
    try {
      const res = (await this.call("state.validateAddress", [address])) as { valid?: boolean } | boolean;
      if (typeof res === "boolean") return res;
      return Boolean(res?.valid);
    } catch {
      return /^[A-Za-z0-9_-]{8,96}$/.test(address);
    }
  }

  async getBalance(address: string): Promise<string> {
    const res = (await this.call("state.getBalance", [address])) as
      | string
      | number
      | { balance?: string | number };
    if (typeof res === "string" || typeof res === "number") return String(res);
    return String(res?.balance ?? "0");
  }

  async getTransaction(txid: string): Promise<AnimicaTx> {
    const res = (await this.call("chain.getTransaction", [txid])) as Partial<AnimicaTx> | null;
    if (!res) throw new AnimicaRpcError("TX_NOT_FOUND", "Transaction not found");
    return { txid, ...res };
  }
}
