import * as React from "react";
import * as DA from "../../../services/da";
import { bytesToHex } from "../../../utils/bytes";
import { downloadText } from "../../../utils/download";

/**
 * DaPanel
 * - Pin a blob to DA: choose namespace, paste text or upload file
 * - Show commitment (NMT root), size, namespace
 * - Retrieve blob and download
 * - Fetch & preview an availability proof (JSON)
 *
 * This panel calls helpers from services/da.ts. To remain compatible with
 * different helper naming, it probes multiple function names at runtime.
 */

type PostResult = {
  commitment: string; // 0x-prefixed hex
  size: number;
  namespace: number;
  receipt?: any;
  nmtRoot?: string; // optional alias for commitment in some backends
};

type ProofResult = any;

export default function DaPanel() {
  const [nsInput, setNsInput] = React.useState("24");
  const [text, setText] = React.useState("");
  const [file, setFile] = React.useState<File | null>(null);
  const [posting, setPosting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const [result, setResult] = React.useState<PostResult | null>(null);
  const [gettingBlob, setGettingBlob] = React.useState(false);
  const [blobBytes, setBlobBytes] = React.useState<Uint8Array | null>(null);

  const [samples, setSamples] = React.useState("40");
  const [gettingProof, setGettingProof] = React.useState(false);
  const [proof, setProof] = React.useState<ProofResult | null>(null);

  const inputRef = React.useRef<HTMLInputElement>(null);

  const onPickFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] || null;
    setFile(f);
    if (f) {
      setText("");
    }
  };

  const clearFile = () => {
    setFile(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const doPost = async () => {
    setPosting(true);
    setError(null);
    setResult(null);
    setBlobBytes(null);
    setProof(null);
    try {
      const ns = parseNamespace(nsInput);
      const data = await resolveBytes(text, file);
      if (!data || data.byteLength === 0) throw new Error("No data provided.");
      const res = await postBlobCompat(ns, data);
      setResult({
        commitment: ensure0x(res.commitment || res.nmtRoot),
        size: res.size ?? data.byteLength,
        namespace: res.namespace ?? ns,
        receipt: res.receipt,
        nmtRoot: ensure0x(res.nmtRoot || res.commitment),
      });
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setPosting(false);
    }
  };

  const doGetBlob = async () => {
    if (!result?.commitment) return;
    setGettingBlob(true);
    setError(null);
    setBlobBytes(null);
    try {
      const bytes = await getBlobCompat(result.commitment);
      setBlobBytes(bytes);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setGettingBlob(false);
    }
  };

  const doGetProof = async () => {
    if (!result?.commitment) return;
    setGettingProof(true);
    setError(null);
    setProof(null);
    try {
      const s = clampInt(samples, 1, 65535);
      const pr = await getProofCompat(result.commitment, s);
      setProof(pr);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setGettingProof(false);
    }
  };

  const downloadBlob = () => {
    if (!blobBytes) return;
    const blob = new Blob([blobBytes], { type: "application/octet-stream" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = (file?.name || "da_blob.bin").replace(/[^a-zA-Z0-9._-]/g, "_");
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const copyCommitment = async () => {
    if (!result?.commitment) return;
    try {
      await navigator.clipboard.writeText(result.commitment);
    } catch {
      /* ignore */
    }
  };

  const copyReceipt = async () => {
    if (!result?.receipt) return;
    await navigator.clipboard.writeText(safeStringify(result.receipt, 2));
  };

  const downloadReceipt = () => {
    if (!result?.receipt) return;
    downloadText("da_receipt.json", safeStringify(result.receipt, 2));
  };

  const downloadProof = () => {
    if (!proof) return;
    downloadText("da_proof.json", safeStringify(proof, 2));
  };

  return (
    <div className="h-full flex flex-col">
      <div className="p-3 border-b border-[color:var(--divider,#e5e7eb)]">
        <div className="flex flex-wrap items-center gap-2">
          <div className="text-sm font-semibold">Data Availability – Pin & Verify</div>
          <span className="ml-auto text-xs text-[color:var(--muted,#6b7280)]">
            Pin a blob, get commitment (NMT root), retrieve & prove availability.
          </span>
        </div>
      </div>

      <div className="p-3 grid grid-cols-1 lg:grid-cols-3 gap-3">
        <Card title="Prepare Blob">
          <div className="space-y-3">
            <label className="block">
              <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-1">
                Namespace (number or 0x-hex)
              </div>
              <input
                value={nsInput}
                onChange={(e) => setNsInput(e.target.value)}
                className="w-full border rounded px-2 py-1 text-sm"
                placeholder="e.g., 24 or 0x18"
              />
            </label>

            <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
              Data
            </div>
            <div className="flex items-center gap-2">
              <input
                ref={inputRef}
                type="file"
                onChange={onPickFile}
                className="text-sm"
              />
              {file && (
                <button
                  onClick={clearFile}
                  className="px-2 py-1 text-xs rounded border border-[color:var(--divider,#e5e7eb)] bg-[color:var(--panel-bg,#f9fafb)] hover:bg-white"
                >
                  Clear
                </button>
              )}
            </div>
            <div className="text-xs text-[color:var(--muted,#6b7280)]">
              Or paste text (ignored if a file is selected):
            </div>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              className="w-full border rounded px-2 py-2 text-sm h-28"
              placeholder="Paste text to pin as a blob..."
            />

            <div className="flex items-center gap-2">
              <button
                onClick={doPost}
                disabled={posting}
                className="px-3 py-1.5 text-xs rounded border border-[color:var(--divider,#e5e7eb)] bg-[color:var(--accent,#0284c7)] text-white hover:opacity-95 disabled:opacity-60"
              >
                {posting ? "Pinning…" : "Pin Blob"}
              </button>
              {error && (
                <span className="text-xs text-red-600">{error}</span>
              )}
            </div>
          </div>
        </Card>

        <Card title="Commitment & Receipt">
          {result ? (
            <div className="space-y-2">
              <Row label="Commitment">
                <code className="text-xs break-all">{result.commitment}</code>
                <button
                  onClick={copyCommitment}
                  className="ml-auto px-2 py-1 text-xs rounded border border-[color:var(--divider,#e5e7eb)] bg-[color:var(--panel-bg,#f9fafb)] hover:bg-white"
                >
                  Copy
                </button>
              </Row>
              <Row label="NMT Root">
                <code className="text-xs break-all">{result.nmtRoot || result.commitment}</code>
              </Row>
              <Row label="Namespace">{result.namespace}</Row>
              <Row label="Size">{formatBytes(result.size)}</Row>

              <div className="mt-2">
                <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-1">
                  Receipt
                </div>
                {result.receipt ? (
                  <div className="rounded border bg-[color:var(--panel-bg,#f9fafb)] p-2">
                    <pre className="text-[0.75rem] whitespace-pre-wrap break-words">
                      {safeStringify(result.receipt, 2)}
                    </pre>
                    <div className="mt-2 flex gap-2">
                      <button
                        onClick={copyReceipt}
                        className="px-2 py-1 text-xs rounded border border-[color:var(--divider,#e5e7eb)] bg-white"
                      >
                        Copy JSON
                      </button>
                      <button
                        onClick={downloadReceipt}
                        className="px-2 py-1 text-xs rounded border border-[color:var(--divider,#e5e7eb)] bg-white"
                      >
                        Download
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="text-xs text-[color:var(--muted,#6b7280)]">—</div>
                )}
              </div>
            </div>
          ) : (
            <div className="text-sm text-[color:var(--muted,#6b7280)]">No commitment yet.</div>
          )}
        </Card>

        <Card title="Retrieve & Prove">
          {result ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <button
                  onClick={doGetBlob}
                  disabled={gettingBlob}
                  className="px-3 py-1.5 text-xs rounded border border-[color:var(--divider,#e5e7eb)] bg-white hover:bg-[color:var(--panel-bg,#f9fafb)] disabled:opacity-60"
                >
                  {gettingBlob ? "Fetching…" : "Get Blob"}
                </button>
                {blobBytes && (
                  <>
                    <span className="text-xs text-[color:var(--muted,#6b7280)]">
                      {formatBytes(blobBytes.byteLength)}
                    </span>
                    <button
                      onClick={downloadBlob}
                      className="ml-auto px-2 py-1 text-xs rounded border border-[color:var(--divider,#e5e7eb)] bg-white"
                    >
                      Download
                    </button>
                  </>
                )}
              </div>

              <div className="border-t pt-2">
                <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-1">
                  Availability Proof
                </div>
                <div className="flex items-center gap-2">
                  <input
                    value={samples}
                    onChange={(e) => setSamples(e.target.value)}
                    className="w-24 border rounded px-2 py-1 text-xs"
                    placeholder="samples"
                  />
                  <button
                    onClick={doGetProof}
                    disabled={gettingProof}
                    className="px-3 py-1.5 text-xs rounded border border-[color:var(--divider,#e5e7eb)] bg-white hover:bg-[color:var(--panel-bg,#f9fafb)] disabled:opacity-60"
                  >
                    {gettingProof ? "Fetching…" : "Get Proof"}
                  </button>
                  {proof && (
                    <button
                      onClick={downloadProof}
                      className="ml-auto px-2 py-1 text-xs rounded border border-[color:var(--divider,#e5e7eb)] bg-white"
                    >
                      Download JSON
                    </button>
                  )}
                </div>
                {proof && (
                  <div className="mt-2 rounded border bg-[color:var(--panel-bg,#f9fafb)] p-2 max-h-48 overflow-auto">
                    <pre className="text-[0.75rem] whitespace-pre-wrap break-words">
                      {safeStringify(proof, 2)}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="text-sm text-[color:var(--muted,#6b7280)]">Pin a blob first.</div>
          )}
        </Card>
      </div>
    </div>
  );
}

/* ----------------------------- compat helpers ---------------------------- */

async function postBlobCompat(
  namespace: number,
  data: Uint8Array
): Promise<{ commitment: string; size?: number; namespace?: number; receipt?: any; nmtRoot?: string }> {
  // Try several naming conventions to interop with services/da.ts variants.
  const mod: any = DA as any;
  if (typeof mod.postBlob === "function") return mod.postBlob(namespace, data);
  if (typeof mod.post === "function") return mod.post(namespace, data);
  if (typeof mod.put === "function") return mod.put(namespace, data);
  if (mod.client?.postBlob) return mod.client.postBlob(namespace, data);
  throw new Error("DA post function not found in services/da.ts");
}

async function getBlobCompat(commitment: string): Promise<Uint8Array> {
  const mod: any = DA as any;
  if (typeof mod.getBlob === "function") return mod.getBlob(commitment);
  if (typeof mod.get === "function") return mod.get(commitment);
  if (mod.client?.getBlob) return mod.client.getBlob(commitment);
  throw new Error("DA get function not found in services/da.ts");
}

async function getProofCompat(commitment: string, samples: number): Promise<any> {
  const mod: any = DA as any;
  if (typeof mod.getProof === "function") return mod.getProof(commitment, { samples });
  if (typeof mod.proof === "function") return mod.proof(commitment, { samples });
  if (mod.client?.getProof) return mod.client.getProof(commitment, { samples });
  throw new Error("DA proof function not found in services/da.ts");
}

/* --------------------------------- UI ----------------------------------- */

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-[color:var(--divider,#e5e7eb)] bg-white p-3">
      <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
        {title}
      </div>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-2">
      <div className="min-w-[100px] text-[0.7rem] uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
        {label}
      </div>
      <div className="flex-1 text-sm flex items-center gap-2">{children}</div>
    </div>
  );
}

/* ------------------------------ pure helpers ----------------------------- */

function ensure0x(hex?: string): string {
  if (!hex) return "";
  return hex.startsWith("0x") ? hex : `0x${hex}`;
}

function parseNamespace(v: string): number {
  const s = (v || "").trim();
  if (!s) throw new Error("Namespace required");
  let n: number;
  if (s.startsWith("0x") || s.startsWith("0X")) {
    n = Number.parseInt(s.slice(2), 16);
  } else if (/^\d+$/.test(s)) {
    n = Number.parseInt(s, 10);
  } else {
    throw new Error("Namespace must be a number or 0x-hex");
  }
  if (!Number.isFinite(n) || n < 0) throw new Error("Invalid namespace");
  return n >>> 0; // uint32
}

async function resolveBytes(text: string, file: File | null): Promise<Uint8Array> {
  if (file) {
    const buf = await file.arrayBuffer();
    return new Uint8Array(buf);
  }
  return new TextEncoder().encode(text || "");
}

function clampInt(v: string, min: number, max: number): number {
  const n = Math.max(min, Math.min(max, Number.parseInt(v || "0", 10) || 0));
  return n;
}

function safeStringify(v: any, spaces = 0): string {
  try {
    return JSON.stringify(
      v,
      (_k, val) => (typeof val === "bigint" ? val.toString() : val),
      spaces
    );
  } catch {
    try {
      return String(v);
    } catch {
      return "<unprintable>";
    }
  }
}

function formatBytes(n: number): string {
  if (!Number.isFinite(n)) return String(n);
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let x = n;
  while (x >= 1024 && i < u.length - 1) {
    x /= 1024;
    i++;
  }
  return `${x.toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
}
