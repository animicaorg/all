/// <reference path="../.astro/types.d.ts" />
/// <reference types="astro/client" />

declare const __ACADEMY_API__: string;
declare const __NODE_RPC__: string;
declare const __REWARD_POOL_ADDR__: string;

interface AnimicaWalletProvider {
  /**
   * Returns the currently selected anim1... address, prompting the user to
   * approve the connection on first call. Resolves after the user confirms.
   */
  connect(): Promise<{ address: string; network: string }>;

  /** Currently selected address or null when not yet connected. */
  getAddress(): Promise<string | null>;

  /** Signs an arbitrary UTF-8 message; returns hex signature. */
  signMessage(message: string): Promise<string>;

  /**
   * Submits a value-transfer transaction. The extension shows the user the
   * destination, amount, and fee for explicit confirmation before signing.
   * Returns the broadcasted transaction hash.
   *
   *   amount         — integer string of base units (1 ANIMICA = 10^9 units)
   *   memo           — optional UTF-8 memo recorded with the tx
   */
  sendTransfer(req: {
    to: string;
    amount: string;
    memo?: string;
  }): Promise<{ txHash: string }>;

  /** Subscribes to address-change notifications from the extension. */
  on(event: "addressChanged" | "networkChanged", cb: (v: string) => void): void;
}

interface Window {
  animica?: AnimicaWalletProvider;
}
