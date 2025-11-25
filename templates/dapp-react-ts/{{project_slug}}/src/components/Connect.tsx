import React, { useCallback, useEffect, useMemo, useState } from "react";
import { getAnimicaProvider } from "../services/provider";
import type { Address } from "../services/sdk";

/**
 * Connect.tsx — Wallet connect widget for Animica-compatible providers.
 *
 * - Detects provider via getAnimicaProvider()
 * - Connects to wallet (animica_requestAccounts → eth_requestAccounts fallback)
 * - Shows account, chain id, and optional live balance
 * - Listens to account/chain changes
 * - Optional "Switch Network" to a target chain id (animica_switchChain → wallet_switchEthereumChain)
 *
 * Drop it in any page:
 *   <Connect targetChainId="0xA11CE" showBalance onConnected={({account}) => ... } />
 */

type ConnectProps = {
  /** Desired chain id (hex like "0xA11CE" or decimal string like "412862"). If provided, shows a switch button on mismatch. */
  targetChainId?: string;
  /** Show native balance of the connected account (default: false) */
  showBalance?: boolean;
  /** Display/parse decimals for balance (default: 18) */
  decimals?: number;
  /** Compact UI (pill) vs full card (default: false) */
  compact?: boolean;
  /** Callback when a wallet is connected */
  onConnected?: (ctx: { account: Address; chainId: string }) => void;
  /** Callback when user disconnects (soft) */
  onDisconnected?: () => void;
};

type BalState = {
  base: bigint | null;
  human: string | null;
};

const BIG10 = (p: number) => {
  let n = 1n;
  for (let i = 0; i < p; i++) n *= 10n;
  return n;
};

function fromBaseUnits(base: bigint, decimals: number): string {
  const d = BIG10(decimals);
  const whole = base / d;
  const frac = base % d;
  const fracStr = frac.toString().padStart(decimals, "0").replace(/0+$/, "");
  return fracStr.length ? `${whole.toString()}.${fracStr}` : whole.toString();
}

function shorten(hex?: string, head = 10, tail = 8) {
  if (!hex) return "";
  if (hex.length <= head + tail + 3) return hex;
  return `${hex.slice(0, head)}…${hex.slice(-tail)}`;
}

function ensureHexChainId(id: string | number | null | undefined): string | null {
  if (id == null) return null;
  if (typeof id === "string") {
    if (id.startsWith("0x") || id.startsWith("0X")) return id;
    const asNum = Number(id);
    if (Number.isFinite(asNum)) return `0x${asNum.toString(16)}`;
    return id; // fallback
  }
  if (typeof id === "number") {
    return `0x${id.toString(16)}`;
  }
  return null;
}

function prettyChain(id: string | null): string {
  if (!id) return "—";
  if (id.startsWith("0x") || id.startsWith("0X")) {
    const dec = parseInt(id, 16);
    return `${id} (${Number.isFinite(dec) ? dec : "?"})`;
  }
  const asNum = Number(id);
  if (Number.isFinite(asNum)) return `${id} (0x${asNum.toString(16)})`;
  return id;
}

export default function Connect({
  targetChainId,
  showBalance = false,
  decimals = 18,
  compact = false,
  onConnected,
  onDisconnected,
}: ConnectProps) {
  const provider = useMemo(() => getAnimicaProvider(), []);
  const [account, setAccount] = useState<Address | "">("");
  const [chainId, setChainId] = useState<string | null>(null);
  const [connected, setConnected] = useState<boolean>(false);
  const [balance, setBalance] = useState<BalState>({ base: null, human: null });
  const [busy, setBusy] = useState<boolean>(false);
  const [err, setErr] = useState<string | null>(null);

  const targetHex = ensureHexChainId(targetChainId ?? null);
  const mismatch = Boolean(targetHex && chainId && targetHex.toLowerCase() !== ensureHexChainId(chainId)?.toLowerCase());

  const fetchChainId = useCallback(async () => {
    try {
      const id =
        (await provider.request<string>({ method: "animica_chainId" })) ??
        (await provider.request<string>({ method: "eth_chainId" }));
      setChainId(typeof id === "string" ? id : String(id));
    } catch {
      setChainId(null);
    }
  }, [provider]);

  const refreshBalance = useCallback(
    async (addr: Address) => {
      if (!showBalance) return;
      try {
        let base: string | null = null;
        try {
          base = await provider.request<string>({ method: "animica_getBalance", params: { address: addr } });
        } catch {
          base = await provider.request<string>({ method: "eth_getBalance", params: [addr, "latest"] });
        }
        const asBig = BigInt(base as unknown as string);
        setBalance({ base: asBig, human: fromBaseUnits(asBig, decimals) });
      } catch {
        setBalance({ base: null, human: null });
      }
    },
    [provider, decimals, showBalance]
  );

  const readAccounts = useCallback(async () => {
    try {
      const accs =
        (await provider.request<Address[]>({ method: "animica_accounts" })) ??
        (await provider.request<Address[]>({ method: "eth_accounts" }));
      const first = accs?.[0] ?? "";
      setAccount(first);
      setConnected(Boolean(first));
      if (first) {
        await refreshBalance(first);
      }
    } catch {
      setAccount("");
      setConnected(false);
    }
  }, [provider, refreshBalance]);

  const connect = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const accs =
        (await provider.request<Address[]>({ method: "animica_requestAccounts" })) ??
        (await provider.request<Address[]>({ method: "eth_requestAccounts" }));
      const first = accs?.[0] ?? "";
      setAccount(first);
      setConnected(Boolean(first));
      await fetchChainId();
      if (first) await refreshBalance(first);
      if (first && chainId) {
        onConnected?.({ account: first, chainId });
      } else if (first) {
        // ensure chain fetched before firing
        const idNow =
          (await provider.request<string>({ method: "animica_chainId" }).catch(() => null)) ??
          (await provider.request<string>({ method: "eth_chainId" }).catch(() => null));
        if (idNow) onConnected?.({ account: first, chainId: typeof idNow === "string" ? idNow : String(idNow) });
      }
    } catch (e: any) {
      setErr(e?.message ?? "Failed to connect.");
    } finally {
      setBusy(false);
    }
  }, [provider, fetchChainId, refreshBalance, onConnected, chainId]);

  const softDisconnect = useCallback(() => {
    // Most injected wallets do not support programmatic disconnect.
    // We clear local UI state and let the user disconnect in the wallet if needed.
    setAccount("");
    setConnected(false);
    setBalance({ base: null, human: null });
    setErr(null);
    onDisconnected?.();
  }, [onDisconnected]);

  const switchNetwork = useCallback(async () => {
    if (!targetHex) return;
    setBusy(true);
    setErr(null);
    try {
      try {
        await provider.request({ method: "animica_switchChain", params: { chainId: targetHex } });
      } catch {
        // EVM-ish fallback API
        await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: targetHex }] });
      }
      await fetchChainId();
    } catch (e: any) {
      setErr(e?.message ?? "Failed to switch network.");
    } finally {
      setBusy(false);
    }
  }, [provider, targetHex, fetchChainId]);

  const copyAddr = useCallback(async () => {
    try {
      if (!account) return;
      await navigator.clipboard.writeText(account);
    } catch {
      // ignore
    }
  }, [account]);

  useEffect(() => {
    void fetchChainId();
    void readAccounts();

    // Listen to provider events (EIP-1193-like)
    // @ts-expect-error provider may not be strictly typed
    if (provider && typeof provider.on === "function") {
      // @ts-expect-error typings optional
      const handleAccounts = (accs: Address[]) => {
        const first = accs?.[0] ?? "";
        setAccount(first);
        setConnected(Boolean(first));
        if (first) void refreshBalance(first);
      };
      // @ts-expect-error typings optional
      const handleChain = async (_: unknown) => {
        await fetchChainId();
        if (account) await refreshBalance(account);
      };
      provider.on("accountsChanged", handleAccounts);
      provider.on("chainChanged", handleChain);
      return () => {
        provider.removeListener?.("accountsChanged", handleAccounts);
        provider.removeListener?.("chainChanged", handleChain);
      };
    }
    return;
  }, [provider, fetchChainId, readAccounts, refreshBalance, account]);

  /* ---------------------------------- UI ----------------------------------- */

  const pillStyle: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
    border: "1px solid #e2e8f0",
    borderRadius: 999,
    padding: "6px 10px",
    background: "white",
  };

  const cardStyle: React.CSSProperties = {
    border: "1px solid #e2e8f0",
    borderRadius: 8,
    padding: 12,
    background: "white",
  };

  const btn: React.CSSProperties = { padding: "8px 12px", cursor: "pointer", fontWeight: 600 };

  const content = !connected ? (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <button style={btn} onClick={connect} disabled={busy}>
        {busy ? "Connecting…" : "Connect Wallet"}
      </button>
      {err && <span style={{ color: "#b45309" }}>⚠ {err}</span>}
    </div>
  ) : (
    <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
      <div style={{ display: "grid", gap: 2 }}>
        <div style={{ fontSize: 12, opacity: 0.7 }}>Account</div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <code style={{ fontFamily: "monospace" }}>{shorten(account)}</code>
          <button onClick={copyAddr} title="Copy address" style={{ padding: "4px 8px", cursor: "pointer" }}>
            Copy
          </button>
        </div>
      </div>
      <div style={{ width: 1, height: 26, background: "#e2e8f0" }} />
      <div style={{ display: "grid", gap: 2 }}>
        <div style={{ fontSize: 12, opacity: 0.7 }}>Chain</div>
        <code style={{ fontFamily: "monospace" }}>{prettyChain(chainId)}</code>
      </div>
      {showBalance && (
        <>
          <div style={{ width: 1, height: 26, background: "#e2e8f0" }} />
          <div style={{ display: "grid", gap: 2 }}>
            <div style={{ fontSize: 12, opacity: 0.7 }}>Balance</div>
            <code style={{ fontFamily: "monospace" }}>{balance.human ?? "—"}</code>
          </div>
          <button
            onClick={() => account && refreshBalance(account)}
            title="Refresh balance"
            style={{ padding: "6px 10px", cursor: "pointer" }}
          >
            Refresh
          </button>
        </>
      )}
      <div style={{ flex: 1 }} />
      {mismatch && targetHex && (
        <button onClick={switchNetwork} style={btn} disabled={busy}>
          {busy ? "Switching…" : `Switch to ${targetHex}`}
        </button>
      )}
      <button onClick={softDisconnect} style={btn}>Disconnect</button>
      {err && <span style={{ color: "#b45309" }}>⚠ {err}</span>}
    </div>
  );

  if (compact) {
    return <div style={pillStyle}>{content}</div>;
  }
  return (
    <section style={cardStyle}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
        <div style={{ fontWeight: 600 }}>Wallet</div>
      </div>
      {content}
    </section>
  );
}
