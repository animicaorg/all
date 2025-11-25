import React, { useCallback, useEffect, useMemo, useState } from "react";
import { getAnimicaProvider } from "../services/provider";
import { createClient, type Address, type Receipt } from "../services/sdk";

/**
 * Send.tsx — A practical sender UI for native transfers and Animica-20 tokens.
 *
 * Features:
 *  - Connect wallet (detects provider methods with graceful fallbacks)
 *  - Show current account, chain id, and live balance
 *  - Native coin transfer with optional data / gas / nonce controls
 *  - Token transfer (Animica-20 style) via contract call (transfer(to, amount))
 *  - Gas/fee estimation helper (best-effort)
 *  - Transaction status: hash, pending/included, receipt fields & logs
 *
 * Assumptions:
 *  - The provider exposes .request({ method, params })
 *  - Preferred methods: animica_* (with Ethereum-compatible fallbacks where sensible)
 *  - Balances/amounts are displayed with a configurable decimals (default 18)
 */

/* ---------------------------------- Types ---------------------------------- */

type Mode = "native" | "token";

type BalState = {
  base: bigint | null; // base units (like wei)
  human: string | null; // humanized string using `decimals`
};

type EstimationState = {
  gas?: string;
  maxFeePerGas?: string; // if EIP-1559-like, otherwise use gasPrice
  gasPrice?: string;
  warning?: string | null;
};

/* --------------------------------- Helpers --------------------------------- */

const BIG10 = (p: number) => {
  // compute 10^p as bigint
  let n = 1n;
  for (let i = 0; i < p; i++) n *= 10n;
  return n;
};

function toBaseUnits(human: string, decimals: number): bigint {
  // robust decimal string -> bigint
  // supports "123", "123.45" (up to `decimals` fractional digits)
  const trimmed = human.trim();
  if (!/^\d+(\.\d+)?$/.test(trimmed)) {
    throw new Error("Amount must be a number (use dot for decimals).");
  }
  const [whole, frac = ""] = trimmed.split(".");
  if (frac.length > decimals) {
    throw new Error(`Too many decimal places (max ${decimals}).`);
  }
  const wholePart = BigInt(whole || "0") * BIG10(decimals);
  const fracPart = BigInt((frac + "0".repeat(decimals)).slice(0, decimals) || "0");
  return wholePart + fracPart;
}

function fromBaseUnits(base: bigint, decimals: number): string {
  const d = BIG10(decimals);
  const whole = base / d;
  const frac = base % d;
  const fracStr = frac.toString().padStart(decimals, "0").replace(/0+$/, "");
  return fracStr.length ? `${whole.toString()}.${fracStr}` : whole.toString();
}

function prettyJSON(x: unknown) {
  try {
    return JSON.stringify(x, null, 2);
  } catch {
    return String(x);
  }
}

function shorten(hex?: string, head = 10, tail = 8) {
  if (!hex) return "";
  if (hex.length <= head + tail + 3) return hex;
  return `${hex.slice(0, head)}…${hex.slice(-tail)}`;
}

/* ---------------------------------- Page ----------------------------------- */

export default function Send() {
  const client = useMemo(() => createClient(), []);
  const provider = useMemo(() => getAnimicaProvider(), []);

  // Wallet/account state
  const [account, setAccount] = useState<Address | "">("");
  const [chainId, setChainId] = useState<string | null>(null);
  const [connected, setConnected] = useState<boolean>(false);

  // Balance
  const [decimals, setDecimals] = useState<number>(18);
  const [balance, setBalance] = useState<BalState>({ base: null, human: null });
  const refreshBalance = useCallback(
    async (addr: Address) => {
      try {
        // Prefer animica_getBalance(address), fallback to eth_getBalance
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
    [provider, decimals]
  );

  // Page mode: native vs token transfer
  const [mode, setMode] = useState<Mode>("native");

  // Native transfer form
  const [to, setTo] = useState<Address | "">("");
  const [amountHuman, setAmountHuman] = useState<string>("");
  const [dataHex, setDataHex] = useState<string>("");
  const [gasLimit, setGasLimit] = useState<string>("");
  const [maxFeePerGas, setMaxFeePerGas] = useState<string>(""); // optional EIP-1559-like
  const [gasPrice, setGasPrice] = useState<string>(""); // legacy
  const [nonce, setNonce] = useState<string>("");

  // Token transfer form
  const [tokenAddress, setTokenAddress] = useState<Address | "">("");
  const [tokenTo, setTokenTo] = useState<Address | "">("");
  const [tokenAmountHuman, setTokenAmountHuman] = useState<string>("");

  // Estimation & results
  const [estim, setEstim] = useState<EstimationState>({});
  const [busyEstimate, setBusyEstimate] = useState<boolean>(false);
  const [busySend, setBusySend] = useState<boolean>(false);

  const [lastHash, setLastHash] = useState<string | null>(null);
  const [lastReceipt, setLastReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState<string | null>(null);

  /* ------------------------------- Wallet wiring ------------------------------ */

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
    setError(null);
    try {
      const accs =
        (await provider.request<Address[]>({ method: "animica_requestAccounts" })) ??
        (await provider.request<Address[]>({ method: "eth_requestAccounts" }));
      const first = accs?.[0] ?? "";
      setAccount(first);
      setConnected(Boolean(first));
      await fetchChainId();
      if (first) await refreshBalance(first);
    } catch (e: any) {
      setError(e?.message ?? "Failed to connect.");
    }
  }, [provider, fetchChainId, refreshBalance]);

  useEffect(() => {
    // Initial state
    void fetchChainId();
    void readAccounts();

    // Listen for account/chain changes if provider supports EIP-1193-ish events
    // @ts-expect-error provider might be any
    if (provider && typeof provider.on === "function") {
      // @ts-expect-error event typings optional
      const handleAccounts = (accs: Address[]) => {
        const first = accs?.[0] ?? "";
        setAccount(first);
        setConnected(Boolean(first));
        if (first) void refreshBalance(first);
      };
      // @ts-expect-error event typings optional
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

  /* ------------------------------- Estimations -------------------------------- */

  const estimate = useCallback(async () => {
    setBusyEstimate(true);
    setEstim({});
    setError(null);
    try {
      if (mode === "native") {
        if (!account) throw new Error("Connect your wallet first.");
        if (!to) throw new Error("Recipient address is required.");
        if (!amountHuman) throw new Error("Amount is required.");

        const value = toBaseUnits(amountHuman, decimals).toString();
        const tx = {
          from: account,
          to,
          value,
          data: dataHex || undefined,
        };

        // Try EIP-1559-like first, then legacy gasPrice
        let gas: string | undefined;
        let mfpg: string | undefined;
        let gprice: string | undefined;
        try {
          gas = await provider.request<string>({ method: "animica_estimateGas", params: tx });
        } catch {
          try {
            gas = await provider.request<string>({ method: "eth_estimateGas", params: [tx] });
          } catch {
            gas = undefined;
          }
        }

        try {
          mfpg = await provider.request<string>({ method: "animica_maxFeePerGas" });
        } catch {
          try {
            gprice = await provider.request<string>({ method: "eth_gasPrice" });
          } catch {
            // ignore
          }
        }

        setEstim({
          gas,
          maxFeePerGas: mfpg,
          gasPrice: gprice,
          warning: !gas ? "Could not estimate gas; you may need to set it manually." : null,
        });
        if (gas) setGasLimit(gas);
        if (mfpg) setMaxFeePerGas(mfpg);
        if (gprice) setGasPrice(gprice);
      } else {
        // token mode estimation: do a contract call estimate if supported
        if (!account) throw new Error("Connect your wallet first.");
        if (!tokenAddress) throw new Error("Token contract address is required.");
        if (!tokenTo) throw new Error("Recipient address is required.");
        if (!tokenAmountHuman) throw new Error("Amount is required.");
        const amount = toBaseUnits(tokenAmountHuman, decimals).toString();

        // Some providers expose animica_contractEstimate
        let gas: string | undefined;
        try {
          gas = await provider.request<string>({
            method: "animica_contractEstimate",
            params: {
              from: account,
              to: tokenAddress,
              abi: [
                {
                  name: "transfer",
                  inputs: [
                    { name: "to", type: "address" },
                    { name: "amount", type: "uint256" },
                  ],
                  outputs: [{ name: "ok", type: "bool" }],
                  stateMutability: "nonpayable",
                },
              ],
              fn: "transfer",
              args: [tokenTo, amount],
            },
          });
        } catch {
          gas = undefined;
        }

        setEstim({
          gas,
          warning: !gas ? "Could not estimate token transfer gas; proceed with caution." : null,
        });
        if (gas) setGasLimit(gas);
      }
    } catch (e: any) {
      setError(e?.message ?? "Failed to estimate.");
    } finally {
      setBusyEstimate(false);
    }
  }, [mode, account, to, amountHuman, decimals, dataHex, provider, tokenAddress, tokenTo, tokenAmountHuman]);

  /* ---------------------------------- Send ----------------------------------- */

  const resetTx = () => {
    setLastHash(null);
    setLastReceipt(null);
    setError(null);
  };

  const waitForReceipt = useCallback(
    async (hash: string) => {
      // Prefer SDK helper if available
      // @ts-expect-error optional
      if (typeof (client as any).waitForReceipt === "function") {
        // eslint-disable-next-line @typescript-eslint/no-unsafe-call
        return (client as any).waitForReceipt(hash, { timeoutMs: 120_000, pollMs: 1_500 });
      }
      // Fallback poll via rpc
      const started = Date.now();
      const timeout = 120_000;
      const pollMs = 1_500;
      // @ts-expect-error rpc optional, template-friendly
      const getReceipt = (h: string) => (client as any).rpc?.("animica_getTransactionReceipt", { hash: h });
      if (!getReceipt) return null;
      // eslint-disable-next-line no-constant-condition
      while (true) {
        // eslint-disable-next-line no-await-in-loop
        const r = await getReceipt(hash);
        if (r) return r as Receipt;
        if (Date.now() - started > timeout) return null;
        // eslint-disable-next-line no-await-in-loop
        await new Promise((res) => setTimeout(res, pollMs));
      }
    },
    [client]
  );

  const sendNative = useCallback(async () => {
    setBusySend(true);
    resetTx();
    try {
      if (!account) throw new Error("Connect your wallet first.");
      if (!to) throw new Error("Recipient address is required.");
      if (!amountHuman) throw new Error("Amount is required.");
      const value = toBaseUnits(amountHuman, decimals).toString();

      const tx: Record<string, unknown> = {
        from: account,
        to,
        value,
      };
      if (dataHex) tx.data = dataHex;
      if (gasLimit) tx.gas = gasLimit;
      if (maxFeePerGas) tx.maxFeePerGas = maxFeePerGas;
      if (gasPrice) tx.gasPrice = gasPrice;
      if (nonce) tx.nonce = nonce;

      let hash: string | undefined;
      let receipt: Receipt | null = null;

      // Try combined "send and wait"
      const tryCombined = async () => {
        try {
          const res = await provider.request<{ hash: string; receipt?: Receipt }>({
            method: "animica_sendTransactionAndWait",
            params: tx,
          });
          hash = res?.hash;
          receipt = res?.receipt ?? null;
          return true;
        } catch {
          return false;
        }
      };

      const combinedOK = await tryCombined();
      if (!combinedOK) {
        // Fallback: plain send then wait
        try {
          const res = await provider.request<{ hash: string }>({
            method: "animica_sendTransaction",
            params: tx,
          });
          hash = res?.hash;
        } catch {
          // Last-ditch Ethereum fallback (shape compatible)
          const res = await provider.request<string>({ method: "eth_sendTransaction", params: [tx] });
          hash = res;
        }
        if (!hash) throw new Error("No transaction hash returned.");
        receipt = await waitForReceipt(hash);
      }

      setLastHash(hash ?? null);
      setLastReceipt(receipt ?? null);
      // Balance refresh after success
      if (account) await refreshBalance(account);
    } catch (e: any) {
      setError(e?.message ?? "Failed to send transaction.");
    } finally {
      setBusySend(false);
    }
  }, [
    account,
    to,
    amountHuman,
    decimals,
    dataHex,
    gasLimit,
    gasPrice,
    maxFeePerGas,
    nonce,
    provider,
    waitForReceipt,
    refreshBalance,
  ]);

  const sendToken = useCallback(async () => {
    setBusySend(true);
    resetTx();
    try {
      if (!account) throw new Error("Connect your wallet first.");
      if (!tokenAddress) throw new Error("Token contract address is required.");
      if (!tokenTo) throw new Error("Recipient address is required.");
      if (!tokenAmountHuman) throw new Error("Amount is required.");

      const amount = toBaseUnits(tokenAmountHuman, decimals).toString();

      let hash: string | undefined;
      let receipt: Receipt | null = null;

      // Prefer combined contract send & wait
      const tryCombined = async () => {
        try {
          const res = await provider.request<{ hash: string; receipt?: Receipt }>({
            method: "animica_contractSendAndWait",
            params: {
              from: account,
              to: tokenAddress,
              abi: [
                {
                  name: "transfer",
                  inputs: [
                    { name: "to", type: "address" },
                    { name: "amount", type: "uint256" },
                  ],
                  outputs: [{ name: "ok", type: "bool" }],
                  stateMutability: "nonpayable",
                },
              ],
              fn: "transfer",
              args: [tokenTo, amount],
              txOpts: {
                gas: gasLimit || undefined,
                maxFeePerGas: maxFeePerGas || undefined,
                gasPrice: gasPrice || undefined,
                nonce: nonce || undefined,
              },
            },
          });
          hash = res?.hash;
          receipt = res?.receipt ?? null;
          return true;
        } catch {
          return false;
        }
      };

      const combinedOK = await tryCombined();
      if (!combinedOK) {
        // Fallback: send then wait
        const res = await provider.request<{ hash: string }>({
          method: "animica_contractSend",
          params: {
            from: account,
            to: tokenAddress,
            abi: [
              {
                name: "transfer",
                inputs: [
                  { name: "to", type: "address" },
                  { name: "amount", type: "uint256" },
                ],
                outputs: [{ name: "ok", type: "bool" }],
                stateMutability: "nonpayable",
              },
            ],
            fn: "transfer",
            args: [tokenTo, amount],
            txOpts: {
              gas: gasLimit || undefined,
              maxFeePerGas: maxFeePerGas || undefined,
              gasPrice: gasPrice || undefined,
              nonce: nonce || undefined,
            },
          },
        });
        hash = res?.hash;
        if (!hash) throw new Error("No transaction hash returned.");
        receipt = await waitForReceipt(hash);
      }

      setLastHash(hash ?? null);
      setLastReceipt(receipt ?? null);
      // Balance refresh — token balance not tracked here; refresh native for fee deduction
      if (account) await refreshBalance(account);
    } catch (e: any) {
      setError(e?.message ?? "Failed to send token transaction.");
    } finally {
      setBusySend(false);
    }
  }, [
    account,
    tokenAddress,
    tokenTo,
    tokenAmountHuman,
    decimals,
    gasLimit,
    gasPrice,
    maxFeePerGas,
    nonce,
    provider,
    waitForReceipt,
    refreshBalance,
  ]);

  /* ---------------------------------- UI ------------------------------------- */

  const labelStyle: React.CSSProperties = { display: "grid", gap: 6, marginTop: 10 };
  const inputStyle: React.CSSProperties = { padding: "8px 10px", fontFamily: "monospace" };
  const cardStyle: React.CSSProperties = { border: "1px solid #e2e8f0", borderRadius: 8, padding: 12 };

  return (
    <div style={{ maxWidth: 1040, margin: "0 auto", padding: 24 }}>
      <header style={{ marginBottom: 12 }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Send</h1>
        <p style={{ margin: "6px 0", opacity: 0.75 }}>
          Transfer native coins or Animica-20 tokens. Use the gas estimator, then send and watch for the receipt.
        </p>
      </header>

      {/* Wallet panel */}
      <section style={{ ...cardStyle, marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 12, alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>Wallet</div>
            <div style={{ display: "grid", gap: 3 }}>
              <div>
                <span style={{ opacity: 0.7 }}>Account:</span>{" "}
                <span style={{ fontFamily: "monospace" }}>{account ? shorten(account) : "—"}</span>
              </div>
              <div>
                <span style={{ opacity: 0.7 }}>Chain ID:</span>{" "}
                <span style={{ fontFamily: "monospace" }}>{chainId ?? "—"}</span>
              </div>
              <div>
                <span style={{ opacity: 0.7 }}>Balance:</span>{" "}
                <span style={{ fontFamily: "monospace" }}>
                  {balance.human != null ? `${balance.human} (decimals=${decimals})` : "—"}
                </span>
              </div>
            </div>
          </div>
          <div>
            {!connected ? (
              <button style={{ padding: "8px 14px", fontWeight: 600, cursor: "pointer" }} onClick={connect}>
                Connect
              </button>
            ) : (
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  style={{ padding: "8px 14px", fontWeight: 600, cursor: "pointer" }}
                  onClick={() => account && refreshBalance(account)}
                  title="Refresh balance"
                >
                  Refresh
                </button>
              </div>
            )}
          </div>
        </div>
        <div style={{ marginTop: 8 }}>
          <label style={labelStyle}>
            <span>Display Decimals</span>
            <input
              type="number"
              min={0}
              max={36}
              value={decimals}
              onChange={(e) => setDecimals(Math.max(0, Math.min(36, Number(e.target.value) || 0)))}
              style={inputStyle}
            />
          </label>
        </div>
      </section>

      {/* Mode switch */}
      <section style={{ marginBottom: 12 }}>
        <div style={{ display: "inline-flex", border: "1px solid #e2e8f0", borderRadius: 8, overflow: "hidden" }}>
          <button
            onClick={() => setMode("native")}
            style={{
              padding: "8px 14px",
              cursor: "pointer",
              fontWeight: 600,
              borderRight: "1px solid #e2e8f0",
              background: mode === "native" ? "#f1f5f9" : "white",
            }}
          >
            Native
          </button>
          <button
            onClick={() => setMode("token")}
            style={{
              padding: "8px 14px",
              cursor: "pointer",
              fontWeight: 600,
              background: mode === "token" ? "#f1f5f9" : "white",
            }}
          >
            Token (Animica-20)
          </button>
        </div>
      </section>

      {/* Native transfer card */}
      {mode === "native" && (
        <section style={{ ...cardStyle, marginBottom: 16 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>Native Transfer</h2>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 10 }}>
            <label style={labelStyle}>
              <span>To (address)</span>
              <input type="text" value={to} onChange={(e) => setTo(e.target.value as Address)} style={inputStyle} />
            </label>
            <label style={labelStyle}>
              <span>Amount (human)</span>
              <input
                type="text"
                value={amountHuman}
                onChange={(e) => setAmountHuman(e.target.value)}
                placeholder={`e.g., 1.5 (decimals=${decimals})`}
                style={inputStyle}
              />
            </label>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            <label style={labelStyle}>
              <span>Data (hex, optional)</span>
              <input
                type="text"
                value={dataHex}
                onChange={(e) => setDataHex(e.target.value)}
                placeholder="0x…"
                style={inputStyle}
              />
            </label>
            <label style={labelStyle}>
              <span>Gas Limit (optional)</span>
              <input
                type="text"
                value={gasLimit}
                onChange={(e) => setGasLimit(e.target.value)}
                placeholder={estim.gas ? `suggested: ${estim.gas}` : "auto"}
                style={inputStyle}
              />
            </label>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            <label style={labelStyle}>
              <span>maxFeePerGas (optional)</span>
              <input
                type="text"
                value={maxFeePerGas}
                onChange={(e) => setMaxFeePerGas(e.target.value)}
                placeholder={estim.maxFeePerGas ? `suggested: ${estim.maxFeePerGas}` : "—"}
                style={inputStyle}
              />
            </label>
            <label style={labelStyle}>
              <span>gasPrice (legacy, optional)</span>
              <input
                type="text"
                value={gasPrice}
                onChange={(e) => setGasPrice(e.target.value)}
                placeholder={estim.gasPrice ? `suggested: ${estim.gasPrice}` : "—"}
                style={inputStyle}
              />
            </label>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            <label style={labelStyle}>
              <span>Nonce (optional)</span>
              <input type="text" value={nonce} onChange={(e) => setNonce(e.target.value)} style={inputStyle} />
            </label>
            <div style={{ display: "grid", alignContent: "end", gap: 8, marginTop: 10 }}>
              <div style={{ fontSize: 12, opacity: 0.7 }}>
                Tip: Fill only the fields you understand. Use <b>Estimate</b> to pre-fill gas/fees.
              </div>
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
            <button
              onClick={estimate}
              disabled={busyEstimate}
              style={{ padding: "8px 14px", cursor: "pointer", fontWeight: 600 }}
            >
              {busyEstimate ? "Estimating…" : "Estimate"}
            </button>
            <button
              onClick={sendNative}
              disabled={busySend || !connected}
              style={{ padding: "8px 14px", cursor: "pointer", fontWeight: 600 }}
            >
              {busySend ? "Sending…" : "Send"}
            </button>
            <button
              onClick={() => {
                setTo("");
                setAmountHuman("");
                setDataHex("");
                setGasLimit("");
                setMaxFeePerGas("");
                setGasPrice("");
                setNonce("");
                setEstim({});
                setError(null);
              }}
              style={{ padding: "8px 14px", cursor: "pointer" }}
            >
              Reset
            </button>
          </div>

          {estim.warning && <div style={{ marginTop: 10, color: "#92400e" }}>⚠ {estim.warning}</div>}
        </section>
      )}

      {/* Token transfer card */}
      {mode === "token" && (
        <section style={{ ...cardStyle, marginBottom: 16 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>Token Transfer (Animica-20)</h2>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 10 }}>
            <label style={labelStyle}>
              <span>Token Contract (address)</span>
              <input
                type="text"
                value={tokenAddress}
                onChange={(e) => setTokenAddress(e.target.value as Address)}
                style={inputStyle}
              />
            </label>
            <label style={labelStyle}>
              <span>To (address)</span>
              <input
                type="text"
                value={tokenTo}
                onChange={(e) => setTokenTo(e.target.value as Address)}
                style={inputStyle}
              />
            </label>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            <label style={labelStyle}>
              <span>Amount (human)</span>
              <input
                type="text"
                value={tokenAmountHuman}
                onChange={(e) => setTokenAmountHuman(e.target.value)}
                placeholder={`e.g., 42 (decimals=${decimals})`}
                style={inputStyle}
              />
            </label>
            <label style={labelStyle}>
              <span>Gas Limit (optional)</span>
              <input
                type="text"
                value={gasLimit}
                onChange={(e) => setGasLimit(e.target.value)}
                placeholder={estim.gas ? `suggested: ${estim.gas}` : "auto"}
                style={inputStyle}
              />
            </label>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            <label style={labelStyle}>
              <span>maxFeePerGas (optional)</span>
              <input
                type="text"
                value={maxFeePerGas}
                onChange={(e) => setMaxFeePerGas(e.target.value)}
                style={inputStyle}
              />
            </label>
            <label style={labelStyle}>
              <span>gasPrice (legacy, optional)</span>
              <input type="text" value={gasPrice} onChange={(e) => setGasPrice(e.target.value)} style={inputStyle} />
            </label>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            <label style={labelStyle}>
              <span>Nonce (optional)</span>
              <input type="text" value={nonce} onChange={(e) => setNonce(e.target.value)} style={inputStyle} />
            </label>
            <div style={{ display: "grid", alignContent: "end", gap: 8, marginTop: 10 }}>
              <div style={{ fontSize: 12, opacity: 0.7 }}>
                Tip: Use <b>Estimate</b> to pre-fill gas for token transfers if supported by your wallet.
              </div>
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
            <button
              onClick={estimate}
              disabled={busyEstimate}
              style={{ padding: "8px 14px", cursor: "pointer", fontWeight: 600 }}
            >
              {busyEstimate ? "Estimating…" : "Estimate"}
            </button>
            <button
              onClick={sendToken}
              disabled={busySend || !connected}
              style={{ padding: "8px 14px", cursor: "pointer", fontWeight: 600 }}
            >
              {busySend ? "Sending…" : "Send"}
            </button>
            <button
              onClick={() => {
                setTokenAddress("");
                setTokenTo("");
                setTokenAmountHuman("");
                setGasLimit("");
                setMaxFeePerGas("");
                setGasPrice("");
                setNonce("");
                setEstim({});
                setError(null);
              }}
              style={{ padding: "8px 14px", cursor: "pointer" }}
            >
              Reset
            </button>
          </div>

          {estim.warning && <div style={{ marginTop: 10, color: "#92400e" }}>⚠ {estim.warning}</div>}
        </section>
      )}

      {/* Results & errors */}
      <section style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div style={cardStyle}>
          <h3 style={{ margin: 0, fontSize: 16 }}>Last Transaction</h3>
          <dl style={{ display: "grid", gridTemplateColumns: "130px 1fr", rowGap: 6, columnGap: 12, marginTop: 8 }}>
            <dt style={{ opacity: 0.7 }}>Hash</dt>
            <dd style={{ margin: 0, fontFamily: "monospace" }} title={lastHash ?? undefined}>
              {lastHash ? shorten(lastHash, 14, 10) : "—"}
            </dd>
            <dt style={{ opacity: 0.7 }}>Status</dt>
            <dd style={{ margin: 0 }}>{lastReceipt?.status ?? (lastHash ? "PENDING…" : "—")}</dd>
            {lastReceipt?.blockNumber != null && (
              <>
                <dt style={{ opacity: 0.7 }}>Block</dt>
                <dd style={{ margin: 0 }}>#{lastReceipt.blockNumber}</dd>
              </>
            )}
            {lastReceipt?.gasUsed != null && (
              <>
                <dt style={{ opacity: 0.7 }}>Gas Used</dt>
                <dd style={{ margin: 0 }}>{lastReceipt.gasUsed}</dd>
              </>
            )}
          </dl>
          {lastReceipt?.logs && Array.isArray(lastReceipt.logs) && lastReceipt.logs.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>Logs</div>
              <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0 }}>
                {prettyJSON(lastReceipt.logs)}
              </pre>
            </div>
          )}
        </div>

        <div style={cardStyle}>
          <h3 style={{ margin: 0, fontSize: 16 }}>Errors & Debug</h3>
          <pre style={{ marginTop: 8, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {error ? `Error: ${error}` : "—"}
          </pre>
          <details style={{ marginTop: 8 }}>
            <summary style={{ cursor: "pointer", fontWeight: 600 }}>Advanced</summary>
            <ul style={{ marginTop: 6 }}>
              <li>
                If estimation fails, you can still try sending with an explicit <code>gas</code> and either{" "}
                <code>maxFeePerGas</code> or <code>gasPrice</code>.
              </li>
              <li>
                If your wallet does not support <code>animica_*</code> methods, switch to the equivalent{" "}
                <code>eth_*</code> calls or use the Contracts page to invoke methods directly.
              </li>
              <li>Nonce is optional; leave blank to let the wallet fill it.</li>
              <li>Data is optional for native transfers; keep it empty for plain value sends.</li>
            </ul>
          </details>
        </div>
      </section>
    </div>
  );
}
