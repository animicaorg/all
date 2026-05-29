import type {
  ConnectResult,
  SendTransactionRequest,
  SentTransaction,
  SignMessageRequest,
  SignedMessage,
  WalletAdapter,
  WalletEventHandler
} from "./types";

export class MockWalletAdapter implements WalletAdapter {
  readonly id = "MOCK" as const;
  readonly label = "Mock Wallet";
  readonly icon = "flask";

  // Dev-only deterministic mock address (bech32m valid; never used in production).
  private address = "anim1zqpx2fqnjhk909pv83mz4y7wg57s5fxnn6np4msnp45eyjdfdezsm5sygmxsx";
  private chainId = 1;
  private listeners = new Set<WalletEventHandler>();
  private connected = false;

  isAvailable(): boolean {
    return true;
  }
  async connect(): Promise<ConnectResult> {
    this.connected = true;
    this.listeners.forEach((l) => l({ type: "accountsChanged", accounts: [this.address] }));
    return { address: this.address, chainId: this.chainId };
  }
  async disconnect(): Promise<void> {
    this.connected = false;
    this.listeners.forEach((l) => l({ type: "disconnect" }));
  }
  async getAccounts(): Promise<string[]> {
    return this.connected ? [this.address] : [];
  }
  async getChainId(): Promise<number | undefined> {
    return this.chainId;
  }
  async getBalance(_a: string): Promise<string | undefined> {
    return "12345.67";
  }
  async signMessage(req: SignMessageRequest): Promise<SignedMessage> {
    let hex = "";
    for (let i = 0; i < req.message.length; i += 1) {
      hex += req.message.charCodeAt(i).toString(16).padStart(2, "0");
    }
    return {
      address: this.address,
      message: req.message,
      signature: `mock_sig_${hex.slice(0, 32)}`,
      algId: 0,
      algName: "mock",
      publicKey: "0x" + "00".repeat(32)
    };
  }
  async sendTransaction(_req: SendTransactionRequest): Promise<SentTransaction> {
    return { txHash: `mock_tx_${Math.random().toString(36).slice(2, 14)}` };
  }
  on(handler: WalletEventHandler): () => void {
    this.listeners.add(handler);
    return () => this.listeners.delete(handler);
  }
}
