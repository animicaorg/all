import React, { useCallback, useEffect, useMemo, useState } from "react";
import { getAnimicaProvider } from "../services/provider";
import { createClient, type Address, type Receipt } from "../services/sdk";

/**
 * Contracts.tsx — A minimal but capable contract playground page.
 *
 * What you can do here:
 *  - Paste a contract manifest (JSON) and a deployed contract address
 *  - Browse functions parsed from the ABI (split into "Read" and "Write")
 *  - Call read/view functions (no wallet signature needed)
 *  - Invoke write functions via the wallet (sign & send, then wait for receipt)
 *  - Inspect returned values, tx hash, receipt, and emitted events
 *
 * Assumptions:
 *  - The Animica provider (window.animica) implements .request({ method, params })
 *  - JSON-RPC supports a call-like request for read calls
 *  - The wallet supports a send-like request for state-changing calls
 *  - The ABI in the manifest follows the Animica contract ABI schema
 */

type AbiParam = { name: string; type: string };
type AbiFn = {
  name: string;
  inputs?: AbiParam[];
  outputs?: AbiParam[];
  stateMutability?: "view" | "pure" | "nonpayable" | "payable";
};
type Manifest = {
  name: string;
  version?: string;
  address?: string;
  abi: AbiFn[];
  // optional metadata fields in real manifests; ignored here
};

type CallResult = {
  returnValue?: unknown;
  raw?: unknown;
  error?: string | null;
};

type SendResult = {
  hash?: string;
  receipt?: Receipt | null;
  error?: string | null;
};

function isView(f: AbiFn) {
  const m = (f.stateMutability ?? "nonpayable").toLowerCase();
  return m === "view" || m === "pure";
}

function jsonPretty(v: unknown) {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function shorten(addr?: string, head = 10, tail = 8) {
  if (!addr) return "";
  if (addr.length <= head + tail + 3) return addr;
  return `${addr.slice(0, head)}…${addr.slice(-tail)}`;
}

export default function Contracts() {
  const client = useMemo(() => createClient(), []);

  const [manifestText, setManifestText] = useState<string>("");
  const [manifest, setManifest] = useState<Manifest | null>(null);

  const [contractAddress, setContractAddress] = useState<Address | "">("");

  // Parsed functions
  const [readFns, setReadFns] = useState<AbiFn[]>([]);
  const [writeFns, setWriteFns] = useState<AbiFn[]>([]);

  // Selected function & dynamic args state
  const [selectedRead, setSelectedRead] = useState<string>("");
  const [selectedWrite, setSelectedWrite] = useState<string>("");
  const [argValues, setArgValues] = useState<Record<string, string>>({});

  // Results
  const [readResult, setReadResult] = useState<CallResult | null>(null);
  const [sendResult, setSendResult] = useState<SendResult | null>(null);

  // Busy flags
  const [calling, setCalling] = useState<boolean>(false);
  const [sending, setSending] = useState<boolean>(false);

  // Surface parse errors / runtime errors
  const [error, setError] = useState<string | null>(null);

  /* ---------------------------- Manifest parsing flow --------------------------- */

  const parseManifest = useCallback(() => {
    setError(null);
    setReadResult(null);
    setSendResult(null);
    try {
      const m = JSON.parse(manifestText) as Manifest;
      if (!m || !Array.isArray(m.abi)) throw new Error("Manifest must include an `abi` array.");
      setManifest(m);
      if (typeof m.address === "string" && m.address.length > 0) {
        setContractAddress(m.address as Address);
      }
      const reads = m.abi.filter(isView).sort((a, b) => a.name.localeCompare(b.name));
      const writes = m.abi.filter((f) => !isView(f)).sort((a, b) => a.name.localeCompare(b.name));
      setReadFns(reads);
      setWriteFns(writes);
      setSelectedRead(reads[0]?.name ?? "");
      setSelectedWrite(writes[0]?.name ?? "");
      // Reset args when manifest changes
      const allInputs = [...reads, ...writes].flatMap((f) => (f.inputs ?? []).map((p) => p.name));
      const initArgs: Record<string, string> = {};
      for (const name of allInputs) {
        initArgs[name] = "";
      }
      setArgValues(initArgs);
    } catch (e: any) {
      setManifest(null);
      setReadFns([]);
      setWriteFns([]);
      setSelectedRead("");
      setSelectedWrite("");
      setArgValues({});
      setError(e?.message || "Failed to parse manifest JSON.");
    }
  }, [manifestText]);

  /* ---------------------------------- Helpers ---------------------------------- */

  // Collect args for a given function from the typed inputs
  const getArgsForFn = useCallback(
    (fnName: string): unknown[] => {
      const fn =
        readFns.find((f) => f.name === fnName) ??
        writeFns.find((f) => f.name === fnName);
      if (!fn) return [];
      const inputs = fn.inputs ?? [];
      // Keep it simple: interpret each arg field as JSON if possible; otherwise string
      return inputs.map((inp) => {
        const raw = argValues[inp.name] ?? "";
        // Try parse JSON (arrays, objects, numbers); fallback to string
        if (raw.trim().length === 0) return "";
        try {
          return JSON.parse(raw);
        } catch {
          return raw;
        }
      });
    },
    [argValues, readFns, writeFns]
  );

  const onArgChange = useCallback((name: string, value: string) => {
    setArgValues((prev) => ({ ...prev, [name]: value }));
  }, []);

  /* --------------------------------- Read call --------------------------------- */

  const doRead = useCallback(async () => {
    if (!manifest || !contractAddress || !selectedRead) return;
    setCalling(true);
    setReadResult(null);
    setError(null);
    try {
      const args = getArgsForFn(selectedRead);
      // Prefer provider path if available; fallback to raw RPC via client
      const provider = getAnimicaProvider();
      // Method name is intentionally descriptive; adjust to your wallet/provider
      const result = await provider
        .request<any>({
          method: "animica_contractCall",
          params: {
            to: contractAddress,
            abi: manifest.abi,
            fn: selectedRead,
            args,
          },
        })
        .catch(async () => {
          // Fallback: try client.rpc
          // The SDK typically exposes a generic rpc; if not, replace with your client's call helper.
          // @ts-expect-error generic rpc passthrough (template-friendly)
          if (typeof (client as any).rpc === "function") {
            // eslint-disable-next-line @typescript-eslint/no-unsafe-call
            return (client as any).rpc("animica_contractCall", {
              to: contractAddress,
              abi: manifest.abi,
              fn: selectedRead,
              args,
            });
          }
          throw new Error("No provider/rpc route for contractCall available.");
        });

      setReadResult({ returnValue: result, raw: result, error: null });
    } catch (e: any) {
      setReadResult({ returnValue: undefined, raw: null, error: e?.message || String(e) });
    } finally {
      setCalling(false);
    }
  }, [client, contractAddress, manifest, selectedRead, getArgsForFn]);

  /* --------------------------------- Write call -------------------------------- */

  const doWrite = useCallback(async () => {
    if (!manifest || !contractAddress || !selectedWrite) return;
    setSending(true);
    setSendResult(null);
    setError(null);
    try {
      const args = getArgsForFn(selectedWrite);
      const provider = getAnimicaProvider();

      // Ask the wallet to create, sign, send, and (optionally) wait for inclusion.
      // Some wallets support "*AndWait" helpers; otherwise we send then poll for a receipt.
      let txHash: string | undefined;
      let receipt: Receipt | null = null;

      // Try a single-shot helper first
      const tryCombined = async () => {
        try {
          const res = await provider.request<{ hash: string; receipt?: Receipt }>({
            method: "animica_contractSendAndWait",
            params: {
              to: contractAddress,
              abi: manifest.abi,
              fn: selectedWrite,
              args,
              waitFor: { timeoutMs: 120_000, pollMs: 1_500 },
            },
          });
          txHash = res?.hash;
          receipt = res?.receipt ?? null;
          return true;
        } catch {
          return false;
        }
      };

      const combinedWorked = await tryCombined();

      if (!combinedWorked) {
        // Fallback: send first
        const sendRes = await provider.request<{ hash: string }>({
          method: "animica_contractSend",
          params: {
            to: contractAddress,
            abi: manifest.abi,
            fn: selectedWrite,
            args,
          },
        });
        txHash = sendRes?.hash;

        // Then: wait/poll for receipt via SDK client if available
        if (txHash) {
          // Prefer client.waitForReceipt if exposed
          // @ts-expect-error optional in SDK
          if (typeof (client as any).waitForReceipt === "function") {
            // eslint-disable-next-line @typescript-eslint/no-unsafe-call
            receipt = await (client as any).waitForReceipt(txHash, {
              timeoutMs: 120_000,
              pollMs: 1_500,
            });
          } else {
            // Minimal inline poller
            const started = Date.now();
            const timeout = 120_000;
            const pollMs = 1_500;
            // @ts-expect-error optional in SDK
            const getReceipt = (h: string) => (client as any).rpc?.("animica_getTransactionReceipt", { hash: h });
            if (!getReceipt) throw new Error("No route to fetch transaction receipt (client.rpc missing).");
            while (Date.now() - started < timeout) {
              // eslint-disable-next-line no-await-in-loop
              const r = await getReceipt(txHash);
              if (r) {
                receipt = r as Receipt;
                break;
              }
              // eslint-disable-next-line no-await-in-loop
              await new Promise((res) => setTimeout(res, pollMs));
            }
          }
        }
      }

      setSendResult({ hash: txHash, receipt: receipt ?? null, error: null });

      // (Optional) You may refresh balances/nonces/etc. here after a successful tx.
    } catch (e: any) {
      setSendResult({ hash: undefined, receipt: null, error: e?.message || String(e) });
    } finally {
      setSending(false);
    }
  }, [client, contractAddress, manifest, selectedWrite, getArgsForFn]);

  /* ------------------------------ Convenience demo ----------------------------- */

  // Pre-fill a tiny example manifest if the text area is empty
  useEffect(() => {
    if (manifestText.trim().length > 0) return;
    const demo: Manifest = {
      name: "Counter",
      version: "1.0.0",
      abi: [
        { name: "get", inputs: [], outputs: [{ name: "value", type: "uint256" }], stateMutability: "view" },
        { name: "inc", inputs: [], outputs: [], stateMutability: "nonpayable" },
        { name: "add", inputs: [{ name: "delta", type: "uint256" }], outputs: [], stateMutability: "nonpayable" },
      ],
    };
    setManifestText(JSON.stringify(demo, null, 2));
  }, [manifestText]);

  /* ---------------------------------- Render UI -------------------------------- */

  const selectedReadFn = useMemo(() => readFns.find((f) => f.name === selectedRead) || null, [readFns, selectedRead]);
  const selectedWriteFn = useMemo(() => writeFns.find((f) => f.name === selectedWrite) || null, [writeFns, selectedWrite]);

  return (
    <div style={{ maxWidth: 1040, margin: "0 auto", padding: 24 }}>
      <header style={{ marginBottom: 12 }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Contract Playground</h1>
        <p style={{ margin: "6px 0", opacity: 0.75 }}>
          Paste a manifest JSON, enter a deployed address, and call functions. This page prefers the wallet provider
          for contract calls; if not available, it attempts a direct RPC fallback.
        </p>
      </header>

      <section style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
        <div style={{ border: "1px solid #e2e8f0", borderRadius: 8, padding: 12 }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
            <h2 style={{ margin: 0, fontSize: 18 }}>Manifest</h2>
            <button
              style={{ padding: "6px 10px", cursor: "pointer" }}
              onClick={parseManifest}
              title="Parse the JSON and extract ABI"
            >
              Parse
            </button>
          </div>
          <textarea
            spellCheck={false}
            value={manifestText}
            onChange={(e) => setManifestText(e.target.value)}
            placeholder='{ "name": "Counter", "abi": [...] }'
            style={{ width: "100%", minHeight: 260, marginTop: 8, fontFamily: "monospace", padding: 10 }}
          />
          {error && (
            <div style={{ color: "#b91c1c", marginTop: 10 }}>
              <strong>Parse error:</strong> {error}
            </div>
          )}
        </div>

        <div style={{ border: "1px solid #e2e8f0", borderRadius: 8, padding: 12 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>Target</h2>
          <label style={{ display: "grid", gap: 6, marginTop: 10 }}>
            <span>Contract Address</span>
            <input
              type="text"
              spellCheck={false}
              placeholder="anim1…"
              value={contractAddress}
              onChange={(e) => setContractAddress(e.target.value as Address)}
              style={{ padding: "8px 10px", fontFamily: "monospace" }}
            />
          </label>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 16 }}>
            <div>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>Read (view)</div>
              {readFns.length === 0 ? (
                <div style={{ opacity: 0.7 }}>No view functions parsed.</div>
              ) : (
                <select
                  value={selectedRead}
                  onChange={(e) => setSelectedRead(e.target.value)}
                  style={{ width: "100%", padding: "6px 10px" }}
                >
                  {readFns.map((f) => (
                    <option key={f.name} value={f.name}>
                      {f.name}
                    </option>
                  ))}
                </select>
              )}
            </div>
            <div>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>Write (tx)</div>
              {writeFns.length === 0 ? (
                <div style={{ opacity: 0.7 }}>No state-changing functions parsed.</div>
              ) : (
                <select
                  value={selectedWrite}
                  onChange={(e) => setSelectedWrite(e.target.value)}
                  style={{ width: "100%", padding: "6px 10px" }}
                >
                  {writeFns.map((f) => (
                    <option key={f.name} value={f.name}>
                      {f.name}
                    </option>
                  ))}
                </select>
              )}
            </div>
          </div>

          {selectedReadFn && selectedReadFn.inputs && selectedReadFn.inputs.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>Inputs for {selectedReadFn.name}</div>
              <div style={{ display: "grid", gap: 8 }}>
                {selectedReadFn.inputs.map((inp) => (
                  <label key={`${selectedReadFn.name}:${inp.name}`} style={{ display: "grid", gap: 4 }}>
                    <span>
                      {inp.name} <small style={{ opacity: 0.6 }}>({inp.type})</small>
                    </span>
                    <input
                      type="text"
                      spellCheck={false}
                      value={argValues[inp.name] ?? ""}
                      onChange={(e) => onArgChange(inp.name, e.target.value)}
                      placeholder='Try JSON: 123, "hello", ["a","b"]'
                      style={{ padding: "6px 10px", fontFamily: "monospace" }}
                    />
                  </label>
                ))}
              </div>
            </div>
          )}

          {selectedWriteFn && selectedWriteFn.inputs && selectedWriteFn.inputs.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>Inputs for {selectedWriteFn.name}</div>
              <div style={{ display: "grid", gap: 8 }}>
                {selectedWriteFn.inputs.map((inp) => (
                  <label key={`${selectedWriteFn.name}:${inp.name}`} style={{ display: "grid", gap: 4 }}>
                    <span>
                      {inp.name} <small style={{ opacity: 0.6 }}>({inp.type})</small>
                    </span>
                    <input
                      type="text"
                      spellCheck={false}
                      value={argValues[inp.name] ?? ""}
                      onChange={(e) => onArgChange(inp.name, e.target.value)}
                      placeholder='Try JSON: 123, "hello", ["a","b"]'
                      style={{ padding: "6px 10px", fontFamily: "monospace" }}
                    />
                  </label>
                ))}
              </div>
            </div>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button
              onClick={doRead}
              disabled={!manifest || !contractAddress || !selectedRead || calling}
              style={{ padding: "8px 14px", cursor: "pointer", fontWeight: 600 }}
              title="Execute a read-only call"
            >
              {calling ? "Calling…" : `Call ${selectedRead || "read"}`}
            </button>
            <button
              onClick={doWrite}
              disabled={!manifest || !contractAddress || !selectedWrite || sending}
              style={{ padding: "8px 14px", cursor: "pointer", fontWeight: 600 }}
              title="Send a state-changing transaction via wallet"
            >
              {sending ? "Sending…" : `Send ${selectedWrite || "write"}`}
            </button>
          </div>
        </div>
      </section>

      <section style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div style={{ border: "1px solid #e2e8f0", borderRadius: 8, padding: 12 }}>
          <h3 style={{ margin: 0, fontSize: 16 }}>Read Result</h3>
          <pre style={{ marginTop: 8, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {readResult
              ? readResult.error
                ? `Error: ${readResult.error}`
                : jsonPretty(readResult.returnValue)
              : "—"}
          </pre>
        </div>

        <div style={{ border: "1px solid #e2e8f0", borderRadius: 8, padding: 12 }}>
          <h3 style={{ margin: 0, fontSize: 16 }}>Last Transaction</h3>
          {sendResult ? (
            <>
              <dl style={{ display: "grid", gridTemplateColumns: "130px 1fr", rowGap: 6, columnGap: 12, marginTop: 8 }}>
                <dt style={{ opacity: 0.7 }}>Hash</dt>
                <dd style={{ margin: 0, fontFamily: "monospace" }} title={sendResult.hash}>
                  {sendResult.hash ? shorten(sendResult.hash, 14, 10) : "—"}
                </dd>
                <dt style={{ opacity: 0.7 }}>Status</dt>
                <dd style={{ margin: 0 }}>{sendResult.receipt?.status ?? (sendResult.hash ? "PENDING…" : "—")}</dd>
                {sendResult.receipt?.blockNumber != null && (
                  <>
                    <dt style={{ opacity: 0.7 }}>Block</dt>
                    <dd style={{ margin: 0 }}>#{sendResult.receipt.blockNumber}</dd>
                  </>
                )}
                {sendResult.receipt?.gasUsed != null && (
                  <>
                    <dt style={{ opacity: 0.7 }}>Gas Used</dt>
                    <dd style={{ margin: 0 }}>{sendResult.receipt.gasUsed}</dd>
                  </>
                )}
              </dl>
              {sendResult.error && (
                <div style={{ marginTop: 10, color: "#b91c1c" }}>
                  <strong>Error:</strong> {sendResult.error}
                </div>
              )}
              {sendResult.receipt?.logs && Array.isArray(sendResult.receipt.logs) && sendResult.receipt.logs.length > 0 && (
                <div style={{ marginTop: 12 }}>
                  <div style={{ fontWeight: 600, marginBottom: 6 }}>Events</div>
                  <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0 }}>
                    {jsonPretty(sendResult.receipt.logs)}
                  </pre>
                </div>
              )}
            </>
          ) : (
            <div style={{ marginTop: 8 }}>—</div>
          )}
        </div>
      </section>

      <section style={{ marginTop: 16 }}>
        <details>
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>Tips & Troubleshooting</summary>
          <ul style={{ marginTop: 8 }}>
            <li>
              If <code>Parse</code> fails, validate your JSON with a linter. The manifest must include{" "}
              <code>abi</code> as an array of function objects.
            </li>
            <li>
              For inputs, you can enter raw values or JSON. For example: <code>123</code>, <code>"hello"</code>,{" "}
              <code>["a","b"]</code>, or <code>{{"{\"k\":1}"}}</code>.
            </li>
            <li>
              If your wallet doesn’t support <code>animica_contractCall</code> /{" "}
              <code>animica_contractSend</code>, rename those in the code to match your provider, or route via the SDK’s
              generic <code>client.rpc</code> helper.
            </li>
            <li>
              For production, pre-load manifests from your build artifacts or explorer APIs instead of pasting by hand.
            </li>
          </ul>
        </details>
      </section>
    </div>
  );
}
