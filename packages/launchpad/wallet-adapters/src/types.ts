import type { WalletType } from "@launchpad/shared";

export interface AnimicaProvider {
  isAnimica?: boolean;
  request(args: { method: string; params?: any[] | Record<string, any> }): Promise<any>;
  on?(event: string, handler: (...args: any[]) => void): void;
  removeListener?(event: string, handler: (...args: any[]) => void): void;
}

declare global {
  interface Window {
    animica?: AnimicaProvider;
  }
}

export type WalletEvent =
  | { type: "accountsChanged"; accounts: string[] }
  | { type: "chainChanged"; chainId: number }
  | { type: "disconnect" };

export type WalletEventHandler = (event: WalletEvent) => void;

export interface ConnectResult {
  address: string;
  chainId?: number;
}

export interface SignMessageRequest {
  message: string;
  purpose?: "auth" | "tx" | "generic";
}

export interface SignedMessage {
  address: string;
  message: string;
  /** 0x-prefixed hex of the raw signature bytes returned by animica_signMessage. */
  signature: string;
  /** Algorithm id for the signing public key (e.g. 0x1001 = Dilithium3). */
  algId: number;
  /** Algorithm name reported by the wallet (e.g. "dilithium3"). */
  algName: string;
  /** 0x-prefixed hex of the signer's public key, required for server-side verification. */
  publicKey: string;
}

export interface SendTransactionRequest {
  method: string;
  params: Record<string, unknown>;
  description: string;
}

export interface SentTransaction {
  txHash: string;
}

export class WalletError extends Error {
  code: string;
  recoverable: boolean;
  constructor(code: string, message: string, opts: { recoverable?: boolean } = {}) {
    super(message);
    this.code = code;
    this.recoverable = opts.recoverable ?? true;
  }
}

export interface WalletAdapter {
  readonly id: WalletType;
  readonly label: string;
  readonly icon: string;
  isAvailable(): boolean | Promise<boolean>;
  connect(opts?: { silent?: boolean }): Promise<ConnectResult>;
  disconnect(): Promise<void>;
  getAccounts(): Promise<string[]>;
  getChainId(): Promise<number | undefined>;
  getBalance(address: string): Promise<string | undefined>;
  signMessage(req: SignMessageRequest): Promise<SignedMessage>;
  sendTransaction(req: SendTransactionRequest): Promise<SentTransaction>;
  watchAsset?(asset: { symbol: string; id?: string }): Promise<boolean>;
  on(handler: WalletEventHandler): () => void;
}
