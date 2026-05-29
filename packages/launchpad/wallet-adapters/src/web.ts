import { URLS } from "@launchpad/shared";
import type {
  ConnectResult,
  SendTransactionRequest,
  SentTransaction,
  SignMessageRequest,
  SignedMessage,
  WalletAdapter,
  WalletEventHandler
} from "./types";
import { WalletError } from "./types";

export const ANIMICA_WALLET_ALLOWED_ORIGIN = new URL(URLS.wallet).origin;

function pickWindowFeatures() {
  const w = 480;
  const h = 720;
  if (typeof window === "undefined") return `width=${w},height=${h}`;
  const left = Math.max(0, (window.screen.width - w) / 2);
  const top = Math.max(0, (window.screen.height - h) / 2);
  return `width=${w},height=${h},left=${left},top=${top},resizable,scrollbars`;
}

/**
 * Server-mediated handshake with wallet.animica.org.
 *
 * Flow:
 *   1. Dapp calls POST /api/wallet/connect/start. Server stores a pending
 *      WalletConnectRequest row and returns `{requestId, popupUrl}`.
 *   2. We open `popupUrl` (which is wallet.animica.org/connect/?request=…&id=…).
 *   3. User approves in the wallet. The wallet's sidecar POSTs the result
 *      to the dapp callback URL embedded in the signed payload.
 *   4. We poll GET /api/wallet/connect/poll?requestId=… every 2s until the
 *      row flips to `approved` or `rejected`.
 *
 * Signing + transactions via the web wallet aren't yet exposed by
 * wallet.animica.org — fall back to a clear error so users either install
 * the extension or use the mock adapter in dev.
 */
export class AnimicaWebWalletAdapter implements WalletAdapter {
  readonly id = "ANIMICA_WEB" as const;
  readonly label = "Animica Web Wallet";
  readonly icon = "globe";

  private connectedAddress: string | null = null;
  private connectedChainId: number | undefined;
  private listeners = new Set<WalletEventHandler>();

  isAvailable(): boolean {
    return typeof window !== "undefined";
  }

  async connect(): Promise<ConnectResult> {
    if (typeof window === "undefined") {
      throw new WalletError("UNAVAILABLE", "Animica Web Wallet requires a browser.");
    }

    // 1. Ask the dapp's server to start a request.
    const startRes = await fetch("/api/wallet/connect/start", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}"
    });
    if (!startRes.ok) {
      throw new WalletError("START_FAILED", `Could not start wallet handshake (${startRes.status}).`);
    }
    const { requestId, popupUrl, expiresAt } = (await startRes.json()) as {
      requestId: string;
      popupUrl: string;
      expiresAt: string;
    };

    // 2. Open the popup.
    const popup = window.open(popupUrl, "animica-wallet", pickWindowFeatures());
    if (!popup) {
      throw new WalletError("POPUP_BLOCKED", "Pop-up was blocked. Allow pop-ups for animica.xyz.");
    }

    // 3. Poll until status changes or expiry.
    const deadline = new Date(expiresAt).getTime();
    while (Date.now() < deadline) {
      // If the user closed the popup before approving, fail fast.
      if (popup.closed) {
        // Give a short grace period in case the sidecar callback is still in flight.
        await sleep(500);
      }
      try {
        const pollRes = await fetch(
          `/api/wallet/connect/poll?requestId=${encodeURIComponent(requestId)}`,
          { cache: "no-store" }
        );
        if (pollRes.ok) {
          const data = (await pollRes.json()) as {
            status: "pending" | "approved" | "rejected" | "expired";
            address?: string | null;
            accounts?: string[];
          };
          if (data.status === "approved" && data.address) {
            try {
              popup.close();
            } catch {}
            this.connectedAddress = data.address;
            this.connectedChainId = undefined;
            this.listeners.forEach((l) =>
              l({ type: "accountsChanged", accounts: [data.address!] })
            );
            return { address: data.address };
          }
          if (data.status === "rejected") {
            try {
              popup.close();
            } catch {}
            throw new WalletError("USER_REJECTED", "Connection rejected in the Animica Web Wallet.");
          }
          if (data.status === "expired") {
            try {
              popup.close();
            } catch {}
            throw new WalletError("EXPIRED", "Wallet connect request expired.");
          }
        }
      } catch (err) {
        if (err instanceof WalletError) throw err;
        // network blip — keep polling
      }
      if (popup.closed) {
        throw new WalletError("USER_CANCELLED", "Wallet popup was closed before approval.");
      }
      await sleep(2_000);
    }
    try {
      popup.close();
    } catch {}
    throw new WalletError("TIMEOUT", "Wallet connect timed out.");
  }

  async disconnect(): Promise<void> {
    this.connectedAddress = null;
    this.connectedChainId = undefined;
    this.listeners.forEach((l) => l({ type: "disconnect" }));
  }

  async getAccounts(): Promise<string[]> {
    return this.connectedAddress ? [this.connectedAddress] : [];
  }

  async getChainId(): Promise<number | undefined> {
    return this.connectedChainId;
  }

  async getBalance(_address: string): Promise<string | undefined> {
    return undefined;
  }

  async signMessage(_req: SignMessageRequest): Promise<SignedMessage> {
    throw new WalletError(
      "WEB_WALLET_NO_SIGN",
      "Signing isn't supported by wallet.animica.org yet. Install the Animica Wallet Extension to sign in."
    );
  }

  async sendTransaction(_req: SendTransactionRequest): Promise<SentTransaction> {
    throw new WalletError(
      "WEB_WALLET_NO_TX",
      "Transactions aren't supported by wallet.animica.org yet. Install the Animica Wallet Extension to trade."
    );
  }

  on(handler: WalletEventHandler): () => void {
    this.listeners.add(handler);
    return () => this.listeners.delete(handler);
  }
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}
