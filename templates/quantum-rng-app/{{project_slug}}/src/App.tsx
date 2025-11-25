import React, { useCallback, useEffect, useMemo, useState } from "react";

/**
 * Quantum RNG demo app
 *
 * What this page does (no private keys required):
 *  - Reads the latest randomness beacon from a node via JSON-RPC (rand.getBeacon)
 *  - Lets you paste (or locally generate) "quantum bytes"
 *  - Mixes beacon ⊕ quantum bytes in the browser using WebCrypto SHA-256 (demo-friendly)
 *  - Shows nice, copyable hex outputs
 *
 * Notes
 *  - The on-chain contract flow (enqueue→prove→consume) is chain-native and requires a wallet.
 *    This template focuses on the off-chain demo path so it runs anywhere with a public RPC.
 *  - If you want to wire wallet flows, search for "TODO(on-chain)" below.
 */

type JsonRpcReq = {
  jsonrpc: "2.0";
  id: number | string;
  method: string;
  params?: unknown;
};

type JsonRpcRes<T = unknown> = {
  jsonrpc: "2.0";
  id: number | string | null;
  result?: T;
  error?: { code: number; message: string; data?: unknown };
};

type BeaconResult = {
  round?: number;
  output?: string; // 0x…
  // Some nodes may return a simpler { output } or { beacon: {…} } — we try to be permissive.
  beacon?: { round?: number; output?: string };
};

const DEFAULT_RPC =
  (import.meta as any)?.env?.VITE_RPC_URL ??
  (typeof location !== "undefined"
    ? `${location.protocol}//${location.host}`
    : "http://localhost:8545");
const DEFAULT_CHAIN_ID =
  (import.meta as any)?.env?.VITE_CHAIN_ID ?? "1";

const HEX_RE = /^0x[0-9a-fA-F]*$/;

function hexToBytes(hex: string): Uint8Array {
  if (!HEX_RE.test(hex)) throw new Error("Expected hex with 0x prefix");
  const h = hex.slice(2);
  if (h.length % 2 !== 0) throw new Error("Hex length must be even");
  const arr = new Uint8Array(h.length / 2);
  for (let i = 0; i < arr.length; i++) {
    arr[i] = parseInt(h.slice(i * 2, i * 2 + 2), 16);
  }
  return arr;
}

function bytesToHex(b: Uint8Array): string {
  const hex: string[] = new Array(b.length);
  for (let i = 0; i < b.length; i++) {
    hex[i] = b[i].toString(16).padStart(2, "0");
  }
  return "0x" + hex.join("");
}

async function sha256(data: Uint8Array): Promise<Uint8Array> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return new Uint8Array(digest);
}

async function jsonRpc<T>(
  rpcUrl: string,
  method: string,
  params?: unknown,
  id: number | string = 1
): Promise<T> {
  const body: JsonRpcReq = { jsonrpc: "2.0", id, method, params };
  const res = await fetch(rpcUrl.replace(/\/+$/, "") + "/rpc", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`RPC HTTP ${res.status}: ${await res.text()}`);
  }
  const payload = (await res.json()) as JsonRpcRes<T>;
  if (payload.error) {
    throw new Error(
      `RPC ${method} error ${payload.error.code}: ${payload.error.message}`
    );
  }
  return payload.result as T;
}

function useInterval(cb: () => void, ms: number | null) {
  useEffect(() => {
    if (ms == null) return;
    const id = setInterval(cb, ms);
    return () => clearInterval(id);
  }, [cb, ms]);
}

function Section(props: { title: string; children: React.ReactNode }) {
  return (
    <section
      style={{
        border: "1px solid var(--border, #e2e8f0)",
        borderRadius: 12,
        padding: 16,
        margin: "12px 0",
      }}
    >
      <h2 style={{ margin: 0, fontSize: 16 }}>{props.title}</h2>
      <div style={{ marginTop: 12 }}>{props.children}</div>
    </section>
  );
}

function Field(props: {
  label: string;
  value: string;
  setValue: (s: string) => void;
  placeholder?: string;
  mono?: boolean;
}) {
  return (
    <label style={{ display: "block", marginBottom: 10 }}>
      <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 4 }}>
        {props.label}
      </div>
      <input
        value={props.value}
        onChange={(e) => props.setValue(e.target.value)}
        placeholder={props.placeholder}
        style={{
          width: "100%",
          padding: "10px 12px",
          borderRadius: 8,
          border: "1px solid var(--border, #e2e8f0)",
          fontFamily: props.mono ? "ui-monospace, SFMono-Regular, Menlo" : "",
        }}
      />
    </label>
  );
}

function Row(props: { children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 12, alignItems: "stretch" }}>
      {props.children}
    </div>
  );
}

function Button(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      style={{
        padding: "10px 14px",
        borderRadius: 8,
        border: "1px solid #cbd5e1",
        background: "#111827",
        color: "white",
        cursor: "pointer",
        ...(props.style || {}),
      }}
    />
  );
}

function Copyable(props: { label: string; value?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(async () => {
    if (!props.value) return;
    await navigator.clipboard.writeText(props.value);
    setCopied(true);
    setTimeout(() => setCopied(false), 900);
  }, [props.value]);
  return (
    <div style={{ margin: "8px 0" }}>
      <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 4 }}>
        {props.label}
      </div>
      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          background: "#0b1020",
          color: "#cbd5e1",
          padding: "10px 12px",
          borderRadius: 8,
          overflowX: "auto",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo",
        }}
      >
        <code style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
          {props.value || "—"}
        </code>
        <Button onClick={copy} style={{ marginLeft: "auto", background: "#1f2937" }}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

export default function App() {
  const [rpcUrl, setRpcUrl] = useState<string>(DEFAULT_RPC);
  const [chainId, setChainId] = useState<string>(String(DEFAULT_CHAIN_ID));
  const [beaconHex, setBeaconHex] = useState<string>("");
  const [beaconRound, setBeaconRound] = useState<number | undefined>(undefined);
  const [beaconLoading, setBeaconLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const [quantumHex, setQuantumHex] = useState<string>(""); // 0x…
  const [quantumLen, setQuantumLen] = useState<string>("32");
  const [mixedHex, setMixedHex] = useState<string>("");

  const [error, setError] = useState<string>("");

  // Fetch latest beacon
  const fetchBeacon = useCallback(async () => {
    setError("");
    setBeaconLoading(true);
    try {
      const res = await jsonRpc<BeaconResult>(rpcUrl, "rand.getBeacon", {});
      const out =
        res.output ??
        res.beacon?.output ??
        (res as any)?.result?.output ??
        (res as any)?.beacon?.output;
      const rnd =
        res.round ??
        res.beacon?.round ??
        (res as any)?.result?.round ??
        (res as any)?.beacon?.round;

      if (!out || !HEX_RE.test(out)) {
        throw new Error(
          "Unexpected beacon shape: " + JSON.stringify(res, null, 2)
        );
      }
      setBeaconHex(out);
      setBeaconRound(typeof rnd === "number" ? rnd : undefined);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBeaconLoading(false);
    }
  }, [rpcUrl]);

  useEffect(() => {
    // Kick off on first load
    fetchBeacon();
  }, [fetchBeacon]);

  useInterval(
    () => {
      if (!autoRefresh) return;
      fetchBeacon();
    },
    autoRefresh ? 5000 : null
  );

  // Locally generate "quantum bytes" (placeholder)
  const genQuantum = useCallback(() => {
    setError("");
    try {
      const n = Math.max(1, Math.min(4096, parseInt(quantumLen || "0", 10) || 32));
      const buf = new Uint8Array(n);
      crypto.getRandomValues(buf);
      setQuantumHex(bytesToHex(buf));
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  }, [quantumLen]);

  // Mix beacon ⊕ quantum bytes using SHA-256(beacon || quantum)
  const doMix = useCallback(async () => {
    setError("");
    setMixedHex("");
    try {
      if (!beaconHex || !HEX_RE.test(beaconHex)) {
        throw new Error("Fetch a beacon first.");
      }
      if (!quantumHex || !HEX_RE.test(quantumHex)) {
        throw new Error("Provide quantum bytes in 0x… hex (or click Generate).");
      }
      const a = hexToBytes(beaconHex);
      const b = hexToBytes(quantumHex);
      const joined = new Uint8Array(a.length + b.length);
      joined.set(a, 0);
      joined.set(b, a.length);
      const digest = await sha256(joined);
      setMixedHex(bytesToHex(digest));
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  }, [beaconHex, quantumHex]);

  const beaconShort = useMemo(() => {
    if (!beaconHex) return "—";
    return `${beaconHex.slice(0, 12)}…${beaconHex.slice(-10)}`;
  }, [beaconHex]);

  return (
    <div
      style={{
        maxWidth: 860,
        margin: "0 auto",
        padding: "24px 16px 64px",
        fontFamily:
          "-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,Ubuntu,Inter,system-ui",
        color: "#0f172a",
      }}
    >
      <header style={{ marginBottom: 12 }}>
        <h1 style={{ margin: "0 0 6px 0", fontSize: 24 }}>Quantum RNG (demo)</h1>
        <div style={{ opacity: 0.7 }}>
          Mix the on-chain randomness beacon with quantum bytes (or a local
          placeholder) — all in your browser.
        </div>
      </header>

      <Section title="Network">
        <Row>
          <div style={{ flex: 3 }}>
            <Field
              label="RPC URL"
              value={rpcUrl}
              setValue={setRpcUrl}
              placeholder="http://localhost:8545"
              mono
            />
          </div>
          <div style={{ flex: 1 }}>
            <Field
              label="Chain ID"
              value={chainId}
              setValue={setChainId}
              placeholder="1"
              mono
            />
          </div>
        </Row>
        <Row>
          <Button onClick={fetchBeacon} disabled={beaconLoading}>
            {beaconLoading ? "Fetching…" : "Fetch beacon"}
          </Button>
          <label
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              paddingLeft: 8,
            }}
          >
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto refresh (5s)
          </label>
        </Row>
        <div style={{ marginTop: 10, fontSize: 14 }}>
          Latest beacon: <strong>{beaconShort}</strong>{" "}
          {beaconRound != null && (
            <span style={{ opacity: 0.7 }}>(round {beaconRound})</span>
          )}
        </div>
        <Copyable label="Beacon (hex)" value={beaconHex} />
      </Section>

      <Section title="Quantum bytes (placeholder or paste real provider output)">
        <Row>
          <div style={{ flex: 1 }}>
            <Field
              label="Length (bytes)"
              value={quantumLen}
              setValue={setQuantumLen}
              placeholder="32"
              mono
            />
          </div>
          <Button onClick={genQuantum} style={{ alignSelf: "flex-end" }}>
            Generate pseudo-quantum bytes
          </Button>
        </Row>
        <Field
          label="Quantum bytes (0x… hex)"
          value={quantumHex}
          setValue={setQuantumHex}
          placeholder="0x…"
          mono
        />
        <Row>
          <Button onClick={doMix}>Mix beacon ⊕ quantum (SHA-256)</Button>
        </Row>
        <Copyable label="Mixed randomness (hex)" value={mixedHex} />
      </Section>

      <Section title="On-chain path (optional)">
        <div style={{ fontSize: 14, lineHeight: 1.45 }}>
          This template focuses on a self-contained demo. For a full on-chain
          flow using the{" "}
          <code style={{ fontFamily: "ui-monospace,Menlo" }}>
            quantum_rng
          </code>{" "}
          contract:
          <ol style={{ margin: "8px 0 0 18px" }}>
            <li>Deploy <code>contracts/examples/quantum_rng</code> to your devnet.</li>
            <li>
              Use your wallet to call <code>request(n_bytes)</code> (this enqueues a
              quantum job via the chain’s capabilities).
            </li>
            <li>
              After the next block, call <code>latest()</code> (or the contract’s
              view) to read the mixed output.
            </li>
          </ol>
          {/* TODO(on-chain): Wire window.animica for sign+send if you want this page to submit txs. */}
        </div>
      </Section>

      {error && (
        <div
          role="alert"
          style={{
            marginTop: 12,
            padding: 12,
            borderRadius: 8,
            background: "#fff1f2",
            color: "#9f1239",
            border: "1px solid #fecdd3",
            fontFamily: "ui-monospace, Menlo",
          }}
        >
          {error}
        </div>
      )}

      <footer style={{ marginTop: 24, opacity: 0.6, fontSize: 12 }}>
        Tip: if your RPC is behind a different path or proxy, adjust the code in
        <code> jsonRpc()</code> to match. This template assumes POST{" "}
        <code>{rpcUrl.replace(/\/+$/, "")}/rpc</code> with method{" "}
        <code>rand.getBeacon</code>.
      </footer>
    </div>
  );
}
