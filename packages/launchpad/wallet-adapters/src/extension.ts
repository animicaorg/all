import {
  AnimicaProvider,
  ConnectResult,
  SendTransactionRequest,
  SentTransaction,
  SignMessageRequest,
  SignedMessage,
  WalletAdapter,
  WalletError,
  WalletEvent,
  WalletEventHandler
} from "./types";

function getProvider(): AnimicaProvider | null {
  if (typeof window === "undefined") return null;
  return window.animica ?? null;
}

async function safeRequest<T>(p: AnimicaProvider, method: string, params?: any): Promise<T> {
  try {
    return (await p.request({ method, params })) as T;
  } catch (err: any) {
    const code = err?.code ? String(err.code) : "WALLET_REQUEST_FAILED";
    if (code === "4001" || /reject/i.test(err?.message ?? "")) {
      throw new WalletError("USER_REJECTED", "Request rejected in Animica Wallet.");
    }
    if (/unsupported/i.test(err?.message ?? "")) {
      throw new WalletError("METHOD_UNSUPPORTED", `Animica Wallet does not support ${method}.`);
    }
    throw new WalletError(code, err?.message || `Animica Wallet request '${method}' failed.`);
  }
}

export class AnimicaExtensionAdapter implements WalletAdapter {
  readonly id = "ANIMICA_EXTENSION" as const;
  readonly label = "Animica Extension";
  readonly icon = "extension";

  isAvailable(): boolean {
    const p = getProvider();
    return !!p && p.isAnimica !== false;
  }

  async connect(opts: { silent?: boolean } = {}): Promise<ConnectResult> {
    const p = getProvider();
    if (!p) throw new WalletError("NOT_INSTALLED", "Animica Wallet Extension not detected.");
    const method = opts.silent ? "animica_accounts" : "animica_requestAccounts";
    const accounts = await safeRequest<string[]>(p, method);
    if (!accounts?.length) {
      throw new WalletError("NO_ACCOUNT", "No Animica account available.");
    }
    let chainId: number | undefined;
    try {
      chainId = await this.getChainId();
    } catch {
      chainId = undefined;
    }
    return { address: accounts[0], chainId };
  }

  async disconnect(): Promise<void> {
    const p = getProvider();
    if (!p) return;
    try {
      await p.request({ method: "animica_disconnect" });
    } catch {
      // optional — extensions may not implement
    }
  }

  async getAccounts(): Promise<string[]> {
    const p = getProvider();
    if (!p) return [];
    try {
      return (await p.request({ method: "animica_accounts" })) ?? [];
    } catch {
      return [];
    }
  }

  async getChainId(): Promise<number | undefined> {
    const p = getProvider();
    if (!p) return undefined;
    try {
      const raw = await p.request({ method: "animica_chainId" });
      const n = typeof raw === "string" ? parseInt(raw, 16) || parseInt(raw, 10) : Number(raw);
      return Number.isFinite(n) ? n : undefined;
    } catch {
      return undefined;
    }
  }

  async getBalance(address: string): Promise<string | undefined> {
    const p = getProvider();
    if (!p) return undefined;
    try {
      const v = await p.request({ method: "animica_getBalance", params: [address] });
      return typeof v === "string" ? v : v?.toString();
    } catch {
      return undefined;
    }
  }

  async signMessage(req: SignMessageRequest): Promise<SignedMessage> {
    const p = getProvider();
    if (!p) throw new WalletError("NOT_INSTALLED", "Animica Wallet Extension not detected.");
    const accounts = await this.getAccounts();
    if (!accounts.length) {
      throw new WalletError("NOT_CONNECTED", "Connect the Animica wallet first.");
    }
    const signerAddress = accounts[0];

    // Fetch the public key for the signing account up front. The Animica node's
    // on-chain Dilithium3 verifier needs pubkey[:32] alongside the signature,
    // so we cannot verify server-side without it. Requires extension >= 0.2.0.
    let pkInfo: { address: string; algId: number; algName: string; publicKey: string };
    try {
      pkInfo = await safeRequest<typeof pkInfo>(p, "animica_getPublicKey", { address: signerAddress });
    } catch (err: any) {
      if (err?.code === "METHOD_UNSUPPORTED") {
        throw new WalletError(
          "EXTENSION_OUTDATED",
          "Update your Animica Wallet Extension to v0.2.0 or newer to sign in here."
        );
      }
      throw err;
    }
    if (!pkInfo?.publicKey || !pkInfo.publicKey.startsWith("0x")) {
      throw new WalletError("NO_PUBKEY", "Animica wallet did not return a public key.");
    }

    const signature = await safeRequest<string>(p, "animica_signMessage", { message: req.message });
    if (typeof signature !== "string" || !signature.startsWith("0x")) {
      throw new WalletError("BAD_SIGNATURE", "Animica wallet returned an invalid signature.");
    }

    return {
      address: signerAddress,
      message: req.message,
      signature,
      algId: pkInfo.algId,
      algName: pkInfo.algName,
      publicKey: pkInfo.publicKey
    };
  }

  async sendTransaction(req: SendTransactionRequest): Promise<SentTransaction> {
    const p = getProvider();
    if (!p) throw new WalletError("NOT_INSTALLED", "Animica Wallet Extension not detected.");
    const txHash = await safeRequest<string>(p, req.method || "animica_sendTransaction", req.params);
    if (!txHash || typeof txHash !== "string") {
      throw new WalletError("BAD_TX_RESPONSE", "Animica Wallet returned no transaction hash.");
    }
    return { txHash };
  }

  async watchAsset(asset: { symbol: string; id?: string }): Promise<boolean> {
    const p = getProvider();
    if (!p) return false;
    try {
      const ok = await p.request({ method: "animica_watchAsset", params: asset });
      return Boolean(ok);
    } catch {
      return false;
    }
  }

  on(handler: WalletEventHandler): () => void {
    const p = getProvider();
    if (!p?.on) return () => {};
    const onAccounts = (accounts: string[]) =>
      handler({ type: "accountsChanged", accounts: accounts ?? [] });
    const onChain = (chainId: string | number) => {
      const id = typeof chainId === "string" ? parseInt(chainId, 16) || parseInt(chainId, 10) : Number(chainId);
      handler({ type: "chainChanged", chainId: id });
    };
    const onDisconnect = () => handler({ type: "disconnect" });
    p.on("accountsChanged", onAccounts);
    p.on("chainChanged", onChain);
    p.on("disconnect", onDisconnect);
    return () => {
      p.removeListener?.("accountsChanged", onAccounts);
      p.removeListener?.("chainChanged", onChain);
      p.removeListener?.("disconnect", onDisconnect);
    };
  }
}

export type { WalletEvent };
