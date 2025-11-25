import * as React from "react";
import * as Provider from "../../services/provider";
import * as Rpc from "../../services/rpc";
import { bytesFromHex, hexFromBytes } from "../../utils/bytes";
import { downloadText } from "../../utils/download";
import { safeJsonParse } from "../../utils/schema"; // falls back to JSON.parse if not present
import { formatAddress } from "../../utils/format"; // no-op fallback below if missing

// SDK (workspace) — keep imports flexible to work across build setups
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore
import * as TxBuild from "@animica/sdk/tx/build";
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore
import * as TxSend from "@animica/sdk/tx/send";

/**
 * DeployPage
 * - Connect wallet (Animica browser extension or injected provider)
 * - Load manifest (JSON) + code (hex/file)
 * - Build unsigned deploy tx
 * - Estimate gas
 * - Sign and send
 * - Await receipt and show contract address
 *
 * This page is defensive about SDK function names and provider capabilities.
 * It probes multiple names at runtime to remain compatible with nearby code.
 */

type Manifest = Record<string, any>;

type GasEst = { gasLimit: bigint; gasPrice?: bigint } | { gas: bigint; price?: bigint } | any;

type BuildOut = {
  signBytes?: Uint8Array;
  tx?: any;
  message?: any; // alternative name for signable payload
  meta?: any;
};

type SendOut = {
  txHash?: string;
  hash?: string;
  receipt?: any;
};

export default function DeployPage() {
  // wallet
  const [connecting, setConnecting] = React.useState(false);
  const [providerError, setProviderError] = React.useState<string | null>(null);
  const [account, setAccount] = React.useState<string | null>(null);
  const [chainId, setChainId] = React.useState<string | number | null>(null);

  // inputs
  const [manifestText, setManifestText] = React.useState<string>("");
  const [codeHex, setCodeHex] = React.useState<string>("");
  const [value, setValue] = React.useState<string>("0"); // optional native value in wei-like units if supported
  const [nonce, setNonce] = React.useState<string>(""); // optional explicit nonce

  // build + gas
  const [building, setBuilding] = React.useState(false);
  const [built, setBuilt] = React.useState<BuildOut | null>(null);
  const [buildError, setBuildError] = React.useState<string | null>(null);

  const [estimating, setEstimating] = React.useState(false);
  const [gas, setGas] = React.useState<GasEst | null>(null);
  const [estimateError, setEstimateError] = React.useState<string | null>(null);

  // send
  const [sending, setSending] = React.useState(false);
  const [txHash, setTxHash] = React.useState<string | null>(null);
  const [receipt, setReceipt] = React.useState<any | null>(null);
  const [sendError, setSendError] = React.useState<string | null>(null);

  // file inputs
  const manifestFileRef = React.useRef<HTMLInputElement>(null);
  const codeFileRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    // Try eager provider info
    (async () => {
      try {
        const p = await getProvider();
        if (p?.accounts?.length) setAccount(p.accounts[0]);
        if (p?.chainId) setChainId(p.chainId);
      } catch {
        /* ignore */
      }
    })();
  }, []);

  const onConnect = async () => {
    setConnecting(true);
    setProviderError(null);
    try {
      const p = await getProvider();
      const resAcc = await tryGetAccount(p);
      const resChain = await tryGetChainId(p);
      setAccount(resAcc);
      setChainId(resChain);
    } catch (e: any) {
      setProviderError(e?.message || String(e));
    } finally {
      setConnecting(false);
    }
  };

  const onDisconnect = async () => {
    try {
      await Provider.disconnect?.();
    } catch {
      /* noop */
    }
    setAccount(null);
  };

  const onPickManifest = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    const text = await f.text();
    setManifestText(text);
  };

  const onPickCode = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    // Accept raw bytes or hex; if not text, read as ArrayBuffer and hex it.
    if (f.type === "application/json" || f.type.startsWith("text/")) {
      const text = (await f.text()).trim();
      setCodeHex(normalizeHex(text));
      return;
    }
    const buf = new Uint8Array(await f.arrayBuffer());
    setCodeHex("0x" + hexFromBytes(buf));
  };

  const onBuild = async () => {
    setBuilding(true);
    setBuildError(null);
    setBuilt(null);
    setGas(null);
    setEstimateError(null);
    setTxHash(null);
    setReceipt(null);
    setSendError(null);

    try {
      const manifest = parseManifest(manifestText);
      const code = parseCodeHex(codeHex);
      const from = must(account, "Connect a wallet first.");
      const net = await currentNetwork();

      const out = await buildDeployCompat({
        manifest,
        code,
        from,
        chainId: net.chainId,
        value: parseBigish(value || "0"),
        nonce: nonce ? BigInt(nonce) : undefined,
      });

      if (!out.signBytes && !out.message) {
        throw new Error("Builder did not return signable payload (signBytes/message missing).");
      }
      setBuilt(out);
    } catch (e: any) {
      setBuildError(e?.message || String(e));
    } finally {
      setBuilding(false);
    }
  };

  const onEstimateGas = async () => {
    setEstimating(true);
    setEstimateError(null);
    setGas(null);
    try {
      const manifest = parseManifest(manifestText);
      const code = parseCodeHex(codeHex);
      const from = must(account, "Connect a wallet first.");
      const net = await currentNetwork();

      const g = await estimateDeployGasCompat({
        manifest,
        code,
        from,
        chainId: net.chainId,
        value: parseBigish(value || "0"),
      });
      setGas(g);
    } catch (e: any) {
      setEstimateError(e?.message || String(e));
    } finally {
      setEstimating(false);
    }
  };

  const onSignAndSend = async () => {
    setSending(true);
    setSendError(null);
    setTxHash(null);
    setReceipt(null);
    try {
      // If not built yet, build on the fly
      if (!built) {
        await onBuild();
      }
      const builtNow = built || ({} as BuildOut);
      const signBytes: Uint8Array =
        builtNow.signBytes ??
        builtNow.message ??
        (() => {
          throw new Error("Missing signable bytes; click Build first.");
        })();

      const p = await getProvider();
      const sig = await signCompat(p, signBytes);

      // Try to produce "raw" signed tx for sending (SDK usually has helper)
      const tx = builtNow.tx ?? {};
      const sendRes = await sendSignedCompat(tx, signBytes, sig);
      const hash = sendRes.txHash || sendRes.hash || (await deriveHashFallback(tx, signBytes, sig));
      if (hash) setTxHash(hash);

      // Await receipt (SDK or RPC helper)
      const rcpt = await awaitReceiptCompat(hash);
      setReceipt(rcpt);
    } catch (e: any) {
      setSendError(e?.message || String(e));
    } finally {
      setSending(false);
    }
  };

  const exampleLoad = async () => {
    // Try to fetch a local template (if available)
    try {
      const mod = await import("../../services/templates");
      const tpl = await (mod.getTemplate?.("counter") || mod.loadTemplate?.("counter"));
      if (tpl?.manifest) setManifestText(JSON.stringify(tpl.manifest, null, 2));
      if (tpl?.codeHex) setCodeHex(normalizeHex(tpl.codeHex));
    } catch {
      // Fallback: minimal manifest scaffold
      setManifestText(
        JSON.stringify(
          {
            name: "Counter",
            version: "1.0.0",
            abi: {
              constructors: [{ name: "init", inputs: [] }],
              functions: [{ name: "inc", inputs: [], outputs: [] }, { name: "get", inputs: [], outputs: [{ type: "u64" }] }],
              events: [{ name: "Incremented", inputs: [{ name: "by", type: "u64", indexed: false }] }],
            },
            metadata: { author: "you", license: "MIT" },
          },
          null,
          2
        )
      );
      setCodeHex("0x");
    }
  };

  const downloadBuilt = () => {
    if (!built) return;
    const out = {
      ...built,
      signBytes: built.signBytes ? "0x" + hexFromBytes(built.signBytes) : undefined,
      message: built.message ? "0x" + hexFromBytes(built.message) : undefined,
    };
    downloadText("unsigned_deploy.json", JSON.stringify(out, null, 2));
  };

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="p-3 border-b border-[color:var(--divider,#e5e7eb)]">
        <div className="flex items-center gap-3">
          <div className="text-sm font-semibold">Deploy a Contract</div>
          <span className="ml-auto text-xs text-[color:var(--muted,#6b7280)]">
            Provide manifest + code, connect a wallet, build → sign → send.
          </span>
        </div>
      </div>

      <div className="p-3 grid grid-cols-1 xl:grid-cols-3 gap-3">
        {/* Wallet */}
        <Card title="Wallet">
          <div className="space-y-2">
            <Row label="Status">
              {account ? (
                <span className="text-sm">Connected</span>
              ) : (
                <span className="text-sm">Not connected</span>
              )}
            </Row>
            <Row label="Address">
              <code className="text-xs break-all">{account ? prettyAddr(account) : "—"}</code>
            </Row>
            <Row label="Chain">
              <code className="text-xs break-all">{chainId ?? "—"}</code>
            </Row>
            <div className="flex gap-2 pt-2">
              {!account ? (
                <button
                  className="px-3 py-1.5 text-xs rounded border bg-[color:var(--accent,#0284c7)] text-white disabled:opacity-60"
                  onClick={onConnect}
                  disabled={connecting}
                >
                  {connecting ? "Connecting…" : "Connect"}
                </button>
              ) : (
                <button
                  className="px-3 py-1.5 text-xs rounded border bg-white"
                  onClick={onDisconnect}
                >
                  Disconnect
                </button>
              )}
              <button className="px-3 py-1.5 text-xs rounded border bg-white" onClick={exampleLoad}>
                Load Example
              </button>
            </div>
            {providerError && <div className="text-xs text-red-600">{providerError}</div>}
          </div>
        </Card>

        {/* Inputs */}
        <Card title="Inputs">
          <div className="space-y-3">
            <div>
              <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-1">
                Manifest (JSON)
              </div>
              <textarea
                className="w-full border rounded px-2 py-2 text-sm h-40"
                value={manifestText}
                onChange={(e) => setManifestText(e.target.value)}
                placeholder='{"name":"MyContract","abi":{...}}'
              />
              <div className="flex items-center gap-2 mt-1">
                <input ref={manifestFileRef} type="file" accept=".json,application/json" onChange={onPickManifest} />
                <button
                  onClick={() => {
                    if (manifestFileRef.current) manifestFileRef.current.value = "";
                    setManifestText("");
                  }}
                  className="px-2 py-1 text-xs rounded border bg-white"
                >
                  Clear
                </button>
              </div>
            </div>

            <div>
              <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-1">
                Code (hex or binary file)
              </div>
              <textarea
                className="w-full border rounded px-2 py-2 text-sm h-24"
                value={codeHex}
                onChange={(e) => setCodeHex(e.target.value)}
                placeholder="0xdeadbeef..."
              />
              <div className="flex items-center gap-2 mt-1">
                <input ref={codeFileRef} type="file" onChange={onPickCode} />
                <button
                  onClick={() => {
                    if (codeFileRef.current) codeFileRef.current.value = "";
                    setCodeHex("");
                  }}
                  className="px-2 py-1 text-xs rounded border bg-white"
                >
                  Clear
                </button>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-1">Value</div>
                <input
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  className="w-full border rounded px-2 py-1 text-sm"
                  placeholder="0"
                />
              </label>
              <label className="block">
                <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-1">Nonce (optional)</div>
                <input
                  value={nonce}
                  onChange={(e) => setNonce(e.target.value)}
                  className="w-full border rounded px-2 py-1 text-sm"
                  placeholder=""
                />
              </label>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button className="px-3 py-1.5 text-xs rounded border bg-white" onClick={onBuild} disabled={building}>
                {building ? "Building…" : "Build Unsigned Tx"}
              </button>
              <button className="px-3 py-1.5 text-xs rounded border bg-white" onClick={onEstimateGas} disabled={estimating}>
                {estimating ? "Estimating…" : "Estimate Gas"}
              </button>
              {built && (
                <button className="px-3 py-1.5 text-xs rounded border bg-white" onClick={downloadBuilt}>
                  Download Unsigned
                </button>
              )}
              {(buildError || estimateError) && (
                <span className="text-xs text-red-600">{buildError || estimateError}</span>
              )}
            </div>
          </div>
        </Card>

        {/* Send */}
        <Card title="Sign & Send">
          <div className="space-y-3">
            <div className="rounded border bg-[color:var(--panel-bg,#f9fafb)] p-2">
              <KV label="Built">{built ? "Yes" : "No"}</KV>
              <KV label="Gas">
                {gas ? prettyGas(gas) : "—"}
              </KV>
              <KV label="Tx Hash">
                <code className="text-xs break-all">{txHash || "—"}</code>
              </KV>
            </div>

            <div className="flex items-center gap-2">
              <button
                className="px-3 py-1.5 text-xs rounded border bg-[color:var(--accent,#0284c7)] text-white disabled:opacity-60"
                onClick={onSignAndSend}
                disabled={sending || !account}
              >
                {sending ? "Sending…" : "Sign & Send"}
              </button>
              {sendError && <span className="text-xs text-red-600">{sendError}</span>}
            </div>

            <div className="border-t pt-2">
              <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-1">
                Receipt
              </div>
              {receipt ? (
                <div className="rounded border bg-[color:var(--panel-bg,#f9fafb)] p-2 max-h-64 overflow-auto">
                  <pre className="text-[0.75rem] whitespace-pre-wrap break-words">
                    {safeStringify(receipt, 2)}
                  </pre>
                </div>
              ) : (
                <div className="text-xs text-[color:var(--muted,#6b7280)]">—</div>
              )}
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

/* --------------------------------- UI ----------------------------------- */

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-[color:var(--divider,#e5e7eb)] bg-white p-3">
      <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">{title}</div>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2">
      <div className="min-w-[100px] text-[0.7rem] uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
        {label}
      </div>
      <div className="flex-1 text-sm flex items-center gap-2">{children}</div>
    </div>
  );
}

function KV({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 text-sm">
      <div className="min-w-[90px] text-[0.75rem] text-[color:var(--muted,#6b7280)]">{label}</div>
      <div className="flex-1">{children}</div>
    </div>
  );
}

/* ------------------------------ helpers --------------------------------- */

function must<T>(v: T | null | undefined, msg: string): T {
  if (v === null || v === undefined || (typeof v === "string" && v.length === 0)) {
    throw new Error(msg);
  }
  return v;
}

function prettyAddr(a: string) {
  try {
    // Prefer shared formatter if available
    // eslint-disable-next-line @typescript-eslint/ban-ts-comment
    // @ts-ignore
    if (typeof (formatAddress as any) === "function") return (formatAddress as any)(a);
  } catch {}
  return a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-6)}` : a;
}

function normalizeHex(s: string) {
  const t = s.trim();
  if (!t) return "";
  return t.startsWith("0x") || t.startsWith("0X") ? t : "0x" + t.replace(/^0x/i, "");
}

function parseCodeHex(h: string): Uint8Array {
  const s = (h || "").trim();
  if (!s) return new Uint8Array();
  const hh = normalizeHex(s);
  return bytesFromHex(hh);
}

function parseManifest(t: string): Manifest {
  const v = t.trim();
  if (!v) throw new Error("Manifest JSON is required.");
  const obj = (safeJsonParse as any)?.(v) ?? JSON.parse(v);
  if (typeof obj !== "object" || !obj) throw new Error("Manifest must be an object.");
  return obj;
}

function parseBigish(v: string): bigint {
  const s = (v || "").trim();
  if (!s) return 0n;
  if (s.startsWith("0x") || s.startsWith("0X")) return BigInt(s);
  if (/^\d+$/.test(s)) return BigInt(s);
  throw new Error("Value must be decimal or 0x-hex.");
}

function safeStringify(v: any, spaces = 0): string {
  try {
    return JSON.stringify(v, (_k, val) => (typeof val === "bigint" ? val.toString() : val), spaces);
  } catch {
    try {
      return String(v);
    } catch {
      return "<unprintable>";
    }
  }
}

function prettyGas(g: any): string {
  try {
    const gl = g.gasLimit ?? g.gas ?? g.limit ?? g.max ?? null;
    const gp = g.gasPrice ?? g.price ?? null;
    const parts = [];
    if (gl !== null && gl !== undefined) parts.push(`limit ${gl.toString()}`);
    if (gp !== null && gp !== undefined) parts.push(`price ${gp.toString()}`);
    return parts.join(", ");
  } catch {
    return safeStringify(g);
  }
}

/* --------------------------- provider helpers --------------------------- */

async function getProvider(): Promise<any> {
  // Prefer services/provider wrapper if available
  try {
    const p = await (Provider.getProvider?.() ?? (Provider as any).provider?.());
    if (p) return p;
  } catch {
    // ignore
  }
  // Fallback to injected
  // @ts-ignore
  const injected = (globalThis as any).animica || (globalThis as any).ethereum || (globalThis as any).wallet;
  if (!injected) throw new Error("No provider detected. Install/enable the Animica wallet.");
  return injected;
}

async function tryGetAccount(p: any): Promise<string | null> {
  // services/provider may expose accounts array
  if (p?.accounts?.[0]) return p.accounts[0];

  // EIP-1193-ish
  if (p?.request) {
    try {
      const accounts: string[] = await p.request({ method: "eth_requestAccounts" });
      return accounts?.[0] ?? null;
    } catch {
      // try read-only
      try {
        const accounts: string[] = await p.request({ method: "eth_accounts" });
        return accounts?.[0] ?? null;
      } catch {
        /* ignore */
      }
    }
  }

  // services/provider explicit connect
  try {
    const a = await Provider.connect?.();
    if (a) return a.address || a.account || a;
  } catch {
    /* ignore */
  }

  return null;
}

async function tryGetChainId(p: any): Promise<string | number | null> {
  if (p?.chainId) return p.chainId;
  if (p?.request) {
    try {
      const id = await p.request({ method: "eth_chainId" });
      return id;
    } catch {/* ignore */}
  }
  try {
    const info = await Provider.getNetwork?.();
    if (info?.chainId) return info.chainId;
  } catch {/* ignore */}
  return null;
}

async function currentNetwork(): Promise<{ chainId: string | number }> {
  try {
    const info = await Provider.getNetwork?.();
    if (info?.chainId) return { chainId: info.chainId };
  } catch { /* ignore */ }
  const p = await getProvider();
  const id = await tryGetChainId(p);
  if (!id) return { chainId: "0x0" };
  return { chainId: id };
}

/* ------------------------------ SDK compat ------------------------------ */

async function buildDeployCompat(args: {
  manifest: Manifest;
  code: Uint8Array;
  from: string;
  chainId?: string | number;
  value?: bigint;
  nonce?: bigint;
}): Promise<BuildOut> {
  const mod: any = TxBuild as any;

  const fns = [
    "buildDeployTx",
    "deployTx",
    "buildDeploy",
    "deploy",
  ];

  for (const fn of fns) {
    if (typeof mod[fn] === "function") {
      const out = await mod[fn](args);
      return out;
    }
  }

  // Some SDKs expose a namespaced builder
  const cand = mod.Builder || mod.Build || mod;
  for (const fn of fns) {
    if (typeof cand[fn] === "function") {
      const out = await cand[fn](args);
      return out;
    }
  }

  // Last resort — ask services/rpc to help (but still returns signBytes via encode)
  if ((Rpc as any)?.build?.deploy) {
    const out = await (Rpc as any).build.deploy(args);
    return out;
  }

  throw new Error("Deploy builder not found in @animica/sdk/tx/build.");
}

async function estimateDeployGasCompat(args: {
  manifest: Manifest;
  code: Uint8Array;
  from: string;
  chainId?: string | number;
  value?: bigint;
}): Promise<GasEst> {
  const mod: any = TxBuild as any;
  const names = ["estimateDeployGas", "gasEstimateDeploy", "estimateGasDeploy", "estimateDeploy"];
  for (const n of names) {
    if (typeof mod[n] === "function") return mod[n](args);
  }
  const cand = mod.Builder || mod.Build || mod;
  for (const n of names) {
    if (typeof cand[n] === "function") return cand[n](args);
  }
  // Fallback to RPC helper if present
  if (typeof (Rpc as any)?.estimateDeployGas === "function") return (Rpc as any).estimateDeployGas(args);
  throw new Error("Gas estimator not found.");
}

async function sendSignedCompat(tx: any, signBytes: Uint8Array, signature: Uint8Array): Promise<SendOut> {
  const mod: any = TxSend as any;
  const names = ["sendSigned", "sendSignedTx", "broadcastSigned", "sendRawTransaction"];
  for (const n of names) {
    if (typeof mod[n] === "function") return mod[n]({ tx, signBytes, signature });
  }

  // Some SDKs expect a raw concatenation/CBOR bundle
  if (typeof (mod as any).bundleAndSend === "function") {
    return (mod as any).bundleAndSend({ tx, signBytes, signature });
  }

  // Services RPC fallback
  if (typeof (Rpc as any)?.sendSigned === "function") {
    return (Rpc as any).sendSigned({ tx, signBytes, signature });
  }

  // Absolute last resort: try provider.request
  const prov = await getProvider();
  if (prov?.request) {
    const raw = "0x" + hexFromBytes(signBytes) + "." + hexFromBytes(signature); // dummy container if chain accepts it
    const hash = await prov.request({ method: "animica_sendRaw", params: [raw] });
    return { txHash: hash };
  }

  throw new Error("No way to send signed tx was found.");
}

async function awaitReceiptCompat(txHash?: string | null): Promise<any> {
  if (!txHash) {
    // use SDK wait function that might accept no hash but uses returned receipt
    const mod: any = TxSend as any;
    const names = ["awaitReceipt", "waitForReceipt", "pollReceipt"];
    for (const n of names) {
      if (typeof mod[n] === "function") return mod[n]({ txHash });
    }
    return null;
  }

  const mod: any = TxSend as any;
  const names = ["awaitReceipt", "waitForReceipt", "pollReceipt"];
  for (const n of names) {
    if (typeof mod[n] === "function") return mod[n](txHash);
  }

  // Rpc fallback: poll until found
  const rpcAny: any = Rpc;
  const getRcpt =
    rpcAny.getReceipt ||
    rpcAny.txReceipt ||
    rpcAny.getTransactionReceipt ||
    rpcAny.rpc?.getReceipt?.bind(rpcAny.rpc);
  if (typeof getRcpt === "function") {
    const start = Date.now();
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const rcpt = await getRcpt(txHash);
      if (rcpt) return rcpt;
      await sleep(1200);
      if (Date.now() - start > 120000) throw new Error("Timed out waiting for receipt.");
    }
  }
  return null;
}

async function deriveHashFallback(_tx: any, _signBytes: Uint8Array, _sig: Uint8Array): Promise<string | null> {
  // If the send path didn't return a hash, try to re-query mempool
  try {
    const anyRpc: any = Rpc;
    if (typeof anyRpc.getPendingBySender === "function") {
      const pending = await anyRpc.getPendingBySender();
      if (pending?.[0]?.hash) return pending[0].hash;
    }
  } catch { /* ignore */ }
  return null;
}

async function signCompat(provider: any, signBytes: Uint8Array): Promise<Uint8Array> {
  // services/provider helper
  try {
    if (typeof Provider.sign === "function") {
      const sig = await Provider.sign(signBytes);
      if (sig instanceof Uint8Array) return sig;
      if (typeof sig === "string") return bytesFromHex(sig);
    }
  } catch { /* ignore */ }

  // EIP-191/-712-ish request
  if (provider?.request) {
    try {
      const hex = await provider.request({
        method: "animica_sign",
        params: ["0x" + hexFromBytes(signBytes)],
      });
      if (typeof hex === "string") return bytesFromHex(hex);
    } catch { /* ignore */ }
  }

  // Fallback: provider.sign
  if (typeof provider?.sign === "function") {
    const r = await provider.sign(signBytes);
    if (r instanceof Uint8Array) return r;
    if (typeof r === "string") return bytesFromHex(r);
  }

  throw new Error("Provider does not support signing.");
}

function sleep(ms: number) {
  return new Promise((res) => setTimeout(res, ms));
}
