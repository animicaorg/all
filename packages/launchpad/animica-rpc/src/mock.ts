import { NETWORK } from "@launchpad/shared";
import type { AnimicaNetworkInfo, AnimicaTx } from "@launchpad/shared";
import type { AnimicaRpcClient } from "./types";

export class MockAnimicaRpc implements AnimicaRpcClient {
  private startedAt = Date.now();
  private balances = new Map<string, string>();

  async getHeight(): Promise<number> {
    return Math.floor((Date.now() - this.startedAt) / 6000) + 1_412_000;
  }
  async getNetwork(): Promise<AnimicaNetworkInfo> {
    return {
      chainId: NETWORK.defaultChainId,
      chainName: NETWORK.chainName,
      rpcUrl: NETWORK.defaultRpcUrl,
      height: await this.getHeight()
    };
  }
  async validateAddress(address: string): Promise<boolean> {
    return /^[A-Za-z0-9_-]{8,96}$/.test(address);
  }
  async getBalance(address: string): Promise<string> {
    const cached = this.balances.get(address);
    if (cached) return cached;
    const v = (Math.floor(Math.random() * 50_000) + 100).toString();
    this.balances.set(address, v);
    return v;
  }
  async getTransaction(txid: string): Promise<AnimicaTx> {
    return {
      txid,
      status: "confirmed",
      amount: "0",
      from: "anim1zqpxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      to: "anim1zqpxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    };
  }
  async sendTransaction(_raw: string): Promise<string> {
    return `mock_tx_${Math.random().toString(36).slice(2, 12)}`;
  }
  async sendToAddress(_address: string, _amount: string): Promise<string> {
    return `mock_tx_${Math.random().toString(36).slice(2, 12)}`;
  }
  async call(_method: string, _params?: unknown[]): Promise<unknown> {
    return null;
  }
}
