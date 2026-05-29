import type { AnimicaNetworkInfo, AnimicaTx } from "@launchpad/shared";

export interface AnimicaRpcClient {
  getHeight(): Promise<number>;
  getNetwork(): Promise<AnimicaNetworkInfo>;
  validateAddress(address: string): Promise<boolean>;
  getBalance(address: string): Promise<string>;
  getWalletBalance?(label: string): Promise<string>;
  getTransaction(txid: string): Promise<AnimicaTx>;
  sendTransaction?(rawTx: string): Promise<string>;
  sendToAddress?(address: string, amount: string, walletLabel?: string): Promise<string>;
  call(method: string, params?: unknown[]): Promise<unknown>;
}

export class AnimicaRpcError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}
