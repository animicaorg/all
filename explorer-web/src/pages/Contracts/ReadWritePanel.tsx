import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

/** Minimal ABI types (EVM-like shape used for UI only) */
type AbiParam = { name?: string; type: string };
type AbiFn = {
  type: "function";
  name: string;
  inputs: AbiParam[];
  outputs?: AbiParam[];
  stateMutability?: "view" | "pure" | "nonpayable" | "payable";
};
type AbiItem = AbiFn | { type: "event" | string; [k: string]: any };

type Hex = `0x${string}`;
type Address = Hex;

type ReadWritePanelProps = {
  address: Address;
  abi?: AbiItem[];
  /** Optional chainId hint for wallet */
  chainId?: number | string;
  /** Optional RPC shim (if your app exposes one) */
  rpc?: {
    request<T = any>(method: string, params?: any[]): Promise<T>;
  };
};

/** Very small provider shim around window.animica / EIP-1193 */
function getProvider(): { request: (args: { method: string; params?: any[] }) => Promise<any> } | undefined {
  const w = globalThis as any;
  return w.animica?.provider ?? w.animica ?? w.ethereum ?? undefined;
}

function isHex(v: any): v is Hex {
  return typeof v === "string" && /^0x[0-9a-fA-F]*$/.test(v);
}
function isAddress(v: any): v is Address {
  return isHex(v) && v.length === 42;
}

function parseByType(type: string, raw: string): any {
  const t = type.trim();
  if (t === "string") return raw;
  if (t.startsWith("uint") || t.startsWith("int")) {
    if (!raw.length) return "0";
    if (/^0x/i.test(raw)) return raw.toLowerCase();
    if (!/^[0-9]+$/.test(raw)) throw new Error(`Expected integer for ${type}`);
    // Return as hex quantity to be chain-agnostic
    return "0x" + BigInt(raw).toString(16);
  }
  if (t === "bool") {
    const s = raw.toLowerCase().trim();
    if (["true", "1", "yes", "y"].includes(s)) return true;
    if (["false", "0", "no", "n"].includes(s)) return false;
    throw new Error(`Expected boolean for ${type}`);
  }
  if (t === "address") {
    if (!isAddress(raw)) throw new Error(`Expected 0x-prefixed address (20 bytes)`);
    return raw.toLowerCase();
  }
  if (t === "bytes" || t.startsWith("bytes")) {
    if (!isHex(raw)) throw new Error(`Expected hex for ${type}`);
    return raw.toLowerCase();
  }
  // Fallback: pass through
  return raw;
}

/** Build a JSON-y args array using ABI types */
function coerceArgs(fn: AbiFn, inputs: string[]): any[] {
  return (fn.inputs || []).map((p, i) => parseByType(p.type, inputs[i] ?? ""));
}

/** Simple field for each ABI input */
function ArgField({
  idx,
  param,
  value,
  onChange,
}: {
  idx: number;
  param: AbiParam;
  value: string;
  onChange: (v: string) => void;
}) {
  const id = `arg-${idx}`;
  const placeholder = (() => {
    switch (true) {
      case param.type === "address":
        return "0x… (address)";
      case param.type.startsWith("uint") || param.type.startsWith("int"):
        return "e.g. 123";
      case param.type === "bool":
        return "true / false";
      case param.type.startsWith("bytes"):
        return "0x… (hex)";
      default:
        return param.type;
    }
  })();

  return (
    <label className="field">
      <div className="label">
        {param.name || `arg${idx}`} <span className="dim mono">:{param.type}</span>
      </div>
      <input
        id={id}
        className="input"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
      />
    </label>
  );
}

export default function ReadWritePanel({ address, abi = [], chainId, rpc }: ReadWritePanelProps) {
  const provider = getProvider();

  const fnsAll = useMemo(
    () => (abi || []).filter((x) => x.type === "function") as AbiFn[],
    [abi]
  );
  const fnsRead = useMemo(
    () => fnsAll.filter((f) => (f.stateMutability === "view" || f.stateMutability === "pure")),
    [fnsAll]
  );
  const fnsWrite = useMemo(
    () => fnsAll.filter((f) => !(f.stateMutability === "view" || f.stateMutability === "pure")),
    [fnsAll]
  );

  const [tab, setTab] = useState<"read" | "write">(fnsRead.length ? "read" : "write");
  const [fnName, setFnName] = useState<string>(fnsRead[0]?.name || fnsWrite[0]?.name || "");
  const fn = useMemo(() => fnsAll.find((f) => f.name === fnName), [fnsAll, fnName]);

  const [argValues, setArgValues] = useState<string[]>([]);
  const [valueWei, setValueWei] = useState<string>("0"); // hex quantity or decimal
  const [gasLimit, setGasLimit] = useState<string>(""); // optional
  const [result, setResult] = useState<any>(undefined);
  const [txHash, setTxHash] = useState<Hex | undefined>();
  const [sending, setSending] = useState(false);
  const [calling, setCalling] = useState(false);
  const [err, setErr] = useState<string | undefined>();
  const [account, setAccount] = useState<Address | undefined>();
  const [walletChainId, setWalletChainId] = useState<string | number | undefined>();
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Reset args when function changes
  useEffect(() => {
    if (!fn) return;
    setArgValues(new Array(fn.inputs?.length ?? 0).fill(""));
    setResult(undefined);
    setErr(undefined);
    setTxHash(undefined);
  }, [fnName]); // eslint-disable-line

  const ensureWallet = useCallback(async () => {
    const prov = getProvider();
    if (!prov) throw new Error("No wallet provider detected. Please install Animica Wallet.");
    const [acct] = await prov.request({ method: "eth_requestAccounts" });
    const ch = await prov.request({ method: "eth_chainId" }).catch(() => undefined);
    if (mounted.current) {
      setAccount(acct?.toLowerCase());
      setWalletChainId(ch);
    }
    if (chainId && ch && String(chainId) !== String(ch)) {
      throw new Error(`Wallet is connected to chainId=${ch}, expected ${chainId}`);
    }
    return prov;
  }, [chainId]);

  const onCall = useCallback(async () => {
    if (!fn) return;
    setCalling(true);
    setErr(undefined);
    setResult(undefined);
    try {
      const args = coerceArgs(fn, argValues);
      // Prefer app RPC shim if present
      const r = rpc
        ? await rpc.request("omni_contractCall", [{ to: address, method: fn.name, args }])
        : await (async () => {
            // Fallback to provider eth_call using a generic method payload
            const prov = getProvider();
            if (!prov) throw new Error("No RPC available. Provide 'rpc' prop or install a wallet.");
            // A very generic Omni-style call (method+args). Adapters should translate to chain encoding.
            // We try multiple common method names for compatibility.
            const payload = { to: address, method: fn.name, args };
            const tryMethods = ["omni_call", "animica_call", "eth_call_abi"];
            let out: any;
            let lastErr: any;
            for (const m of tryMethods) {
              try {
                out = await prov.request({ method: m, params: [payload] });
                lastErr = undefined;
                break;
              } catch (e) {
                lastErr = e;
              }
            }
            if (lastErr) throw lastErr;
            return out;
          })();

      if (mounted.current) setResult(r);
    } catch (e: any) {
      if (mounted.current) setErr(e?.message || String(e));
    } finally {
      if (mounted.current) setCalling(false);
    }
  }, [fn, argValues, rpc, address]);

  const onSend = useCallback(async () => {
    if (!fn) return;
    setSending(true);
    setErr(undefined);
    setTxHash(undefined);
    try {
      const prov = await ensureWallet();
      const args = coerceArgs(fn, argValues);

      // Normalize value
      let value: string | undefined;
      if (valueWei && valueWei.trim().length) {
        if (/^0x/i.test(valueWei)) value = valueWei.toLowerCase();
        else value = "0x" + BigInt(valueWei).toString(16);
      }

      const tx = {
        to: address,
        method: fn.name,
        args,
        value,
        gas: gasLimit && gasLimit.length ? (/^0x/i.test(gasLimit) ? gasLimit : "0x" + BigInt(gasLimit).toString(16)) : undefined,
      };

      // Try multiple common send methods for broader compatibility
      const tryMethods = ["omni_sendTransaction", "animica_sendTransaction", "eth_sendTransaction"];
      let hash: Hex | undefined;
      let lastErr: any;
      for (const m of tryMethods) {
        try {
          const out = await prov.request({ method: m, params: [tx] });
          hash = (typeof out === "string" ? out : out?.hash) as Hex;
          lastErr = undefined;
          break;
        } catch (e) {
          lastErr = e;
        }
      }
      if (!hash) throw lastErr || new Error("Failed to send transaction");

      if (mounted.current) setTxHash(hash);
    } catch (e: any) {
      if (mounted.current) setErr(e?.message || String(e));
    } finally {
      if (mounted.current) setSending(false);
    }
  }, [fn, argValues, valueWei, gasLimit, address, ensureWallet]);

  const activeReadFns = fnsRead.length ? fnsRead : [];
  const activeWriteFns = fnsWrite.length ? fnsWrite : [];

  return (
    <section className="card">
      <div className="card-header">
        <div className="tabs">
          <button
            className={`tab ${tab === "read" ? "active" : ""}`}
            onClick={() => {
              setTab("read");
              if (activeReadFns.length) setFnName(activeReadFns[0].name);
            }}
          >
            Read
          </button>
          <button
            className={`tab ${tab === "write" ? "active" : ""}`}
            onClick={() => {
              setTab("write");
              if (activeWriteFns.length) setFnName(activeWriteFns[0].name);
            }}
          >
            Write
          </button>
        </div>
        <div className="card-actions">
          {account ? (
            <span className="dim small">
              Connected: <span className="mono">{short(account)}</span>
              {walletChainId !== undefined ? (
                <> • chainId <span className="mono">{String(walletChainId)}</span></>
              ) : null}
            </span>
          ) : (
            <button className="btn small" onClick={() => ensureWallet().catch((e) => setErr(e.message))}>
              Connect Wallet
            </button>
          )}
        </div>
      </div>

      <div className="card-body">
        {err ? <div className="alert warn">{err}</div> : null}

        {tab === "read" ? (
          <>
            {activeReadFns.length ? (
              <MethodForm
                methods={activeReadFns}
                fnName={fnName}
                setFnName={setFnName}
                argValues={argValues}
                setArgValues={setArgValues}
              />
            ) : (
              <p className="dim">No read-only methods available.</p>
            )}

            <div className="mt-2">
              <button className="btn" disabled={!fn || calling} onClick={onCall}>
                {calling ? "Calling…" : "Call"}
              </button>
            </div>

            <div className="mt-3">
              <div className="label">Result</div>
              {result === undefined ? (
                <div className="dim">—</div>
              ) : typeof result === "string" || typeof result === "number" || typeof result === "boolean" ? (
                <pre className="code">{String(result)}</pre>
              ) : (
                <pre className="code">{JSON.stringify(result, null, 2)}</pre>
              )}
            </div>
          </>
        ) : (
          <>
            {activeWriteFns.length ? (
              <>
                <MethodForm
                  methods={activeWriteFns}
                  fnName={fnName}
                  setFnName={setFnName}
                  argValues={argValues}
                  setArgValues={setArgValues}
                />

                <div className="grid grid-cols-1 md:grid-cols-2 gap-16">
                  <label className="field">
                    <div className="label">
                      Value (wei) <span className="dim">(optional)</span>
                    </div>
                    <input
                      className="input"
                      placeholder="0"
                      value={valueWei}
                      onChange={(e) => setValueWei(e.target.value)}
                      spellCheck={false}
                    />
                  </label>

                  <label className="field">
                    <div className="label">
                      Gas limit <span className="dim">(optional)</span>
                    </div>
                    <input
                      className="input"
                      placeholder="e.g. 200000"
                      value={gasLimit}
                      onChange={(e) => setGasLimit(e.target.value)}
                      spellCheck={false}
                    />
                  </label>
                </div>

                <div className="mt-2">
                  <button className="btn primary" disabled={!fn || sending} onClick={onSend}>
                    {sending ? "Sending…" : "Send Transaction"}
                  </button>
                </div>

                <div className="mt-3">
                  <div className="label">Transaction</div>
                  {txHash ? (
                    <pre className="code mono">{txHash}</pre>
                  ) : (
                    <div className="dim">—</div>
                  )}
                </div>
              </>
            ) : (
              <p className="dim">No state-changing methods available.</p>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function MethodForm({
  methods,
  fnName,
  setFnName,
  argValues,
  setArgValues,
}: {
  methods: AbiFn[];
  fnName: string;
  setFnName: (v: string) => void;
  argValues: string[];
  setArgValues: (v: string[]) => void;
}) {
  const fn = useMemo(() => methods.find((f) => f.name === fnName) ?? methods[0], [methods, fnName]);

  useEffect(() => {
    if (!fn) return;
    setArgValues((arr) => {
      const need = fn.inputs?.length ?? 0;
      if (arr.length === need) return arr;
      return new Array(need).fill("");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fn?.name]);

  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-16">
        <label className="field">
          <div className="label">Method</div>
          <select
            className="input"
            value={fn?.name ?? ""}
            onChange={(e) => setFnName(e.target.value)}
          >
            {methods.map((m) => (
              <option value={m.name} key={m.name}>
                {m.name}
                {m.stateMutability ? ` (${m.stateMutability})` : ""}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-16 mt-2">
        {(fn?.inputs || []).map((p, i) => (
          <ArgField
            key={`${fn?.name}-arg-${i}`}
            idx={i}
            param={p}
            value={argValues[i] ?? ""}
            onChange={(v) => {
              const next = argValues.slice();
              next[i] = v;
              setArgValues(next);
            }}
          />
        ))}
      </div>
    </>
  );
}

function short(h: string, n = 4) {
  if (!h) return "";
  if (h.length <= 2 + n * 2) return h;
  return `${h.slice(0, 2 + n)}…${h.slice(-n)}`;
}
