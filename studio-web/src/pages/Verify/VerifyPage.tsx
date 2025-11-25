import * as React from "react";
import { downloadJson } from "../../utils/download";
import { bytesToHex } from "../../utils/bytes";
import { sha3_256 } from "../../utils/hash";
import { useNetwork } from "../../state/network";
import { useToasts } from "../../state/toasts";

/**
 * VerifyPage
 *
 * Upload contract source + manifest (and optional ABI), pick a deployed target
 * (address or tx hash), then ask studio-services to recompile & compare the code
 * hash against chain state. Polls the verify job until completion and renders
 * a friendly verdict (MATCH / MISMATCH) with details.
 *
 * This page talks to studio-services via the servicesApi.verify namespace.
 */

type TargetMode = "address" | "txHash";

type VerifyStatus = "idle" | "queued" | "running" | "succeeded" | "failed";

type VerifyResult = {
  jobId: string;
  targetType: "address" | "txHash";
  target: string;
  compiledCodeHash: string; // hex 0x…
  onChainCodeHash?: string; // hex 0x…
  match: boolean;
  manifestDigest?: string; // informational
  diagnostics?: Array<{ level: "info" | "warning" | "error"; msg: string }>;
  createdAt?: string;
  completedAt?: string;
};

type VerifyPostResponse = { jobId: string };
type VerifyStatusResponse = {
  status: VerifyStatus;
  result?: VerifyResult;
  error?: string;
};

export default function VerifyPage() {
  const [mode, setMode] = React.useState<TargetMode>("address");
  const [target, setTarget] = React.useState("");
  const [sourceFile, setSourceFile] = React.useState<File | null>(null);
  const [manifestFile, setManifestFile] = React.useState<File | null>(null);
  const [abiFile, setAbiFile] = React.useState<File | null>(null);

  const [jobId, setJobId] = React.useState<string | null>(null);
  const [status, setStatus] = React.useState<VerifyStatus>("idle");
  const [result, setResult] = React.useState<VerifyResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const [localHashes, setLocalHashes] = React.useState<{
    source?: string;
    manifest?: string;
    abi?: string;
  }>({});

  const { servicesUrl } = useNetwork();
  const { push } = useToasts();

  // Compute local file digests (UX nicety; not consensus)
  React.useEffect(() => {
    let cancelled = false;
    const run = async () => {
      const out: { source?: string; manifest?: string; abi?: string } = {};
      if (sourceFile) out.source = await digestFile(sourceFile);
      if (manifestFile) out.manifest = await digestFile(manifestFile);
      if (abiFile) out.abi = await digestFile(abiFile);
      if (!cancelled) setLocalHashes(out);
    };
    run().catch(() => void 0);
    return () => {
      cancelled = true;
    };
  }, [sourceFile, manifestFile, abiFile]);

  const busy = status === "queued" || status === "running";

  async function onStart(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);

    try {
      // Basic validation
      if (!target.trim()) throw new Error(`Please enter a ${mode}.`);
      if (!sourceFile) throw new Error("Please provide a contract source file (.py).");
      if (!manifestFile) throw new Error("Please provide a manifest JSON file.");

      const [source, manifestJson, abiJson] = await Promise.all([
        readText(sourceFile),
        readJson(manifestFile),
        abiFile ? readJson(abiFile) : Promise.resolve(undefined),
      ]);

      setStatus("queued");

      // Submit to services
      const resp = await servicesApi.verify.submit(servicesUrl, {
        targetType: mode,
        target: target.trim(),
        source,
        manifest: manifestJson,
        abi: abiJson,
      });
      setJobId(resp.jobId);
      push({ kind: "info", text: `Verification started (job ${resp.jobId.slice(0, 8)}…)` });

      // Poll
      setStatus("running");
      await pollUntilDone(resp.jobId);
    } catch (err: any) {
      const msg = err?.message || "Failed to start verification";
      setError(msg);
      setStatus("failed");
      push({ kind: "error", text: msg });
    }
  }

  async function pollUntilDone(id: string) {
    const started = Date.now();
    const timeoutMs = 90_000;
    const intervalMs = 1200;

    for (;;) {
      const s = await servicesApi.verify.status(servicesUrl, id);
      if (s.status === "succeeded") {
        setStatus("succeeded");
        setResult(s.result || null);
        push({
          kind: s.result?.match ? "success" : "warning",
          text: s.result?.match ? "Verification matched" : "Verification mismatch",
        });
        return;
      }
      if (s.status === "failed") {
        setStatus("failed");
        setError(s.error || "Verification failed");
        return;
      }
      await sleep(intervalMs);
      if (Date.now() - started > timeoutMs) {
        setStatus("failed");
        setError("Verification timed out");
        return;
      }
    }
  }

  function onReset() {
    setJobId(null);
    setStatus("idle");
    setResult(null);
    setError(null);
    setLocalHashes({});
  }

  const verdict =
    status === "succeeded"
      ? result?.match
        ? { label: "MATCH", className: "bg-green-600" }
        : { label: "MISMATCH", className: "bg-yellow-500" }
      : null;

  return (
    <div className="container mx-auto p-4 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Verify Contract Source</h1>
          <p className="text-sm text-[color:var(--muted,#6b7280)]">
            Upload source & manifest, choose the deployed target, and we'll recompile and compare the code hash.
          </p>
        </div>
        {verdict && (
          <span
            className={`text-white text-xs px-2 py-1 rounded ${verdict.className}`}
            title="Verification verdict"
          >
            {verdict.label}
          </span>
        )}
      </header>

      <form onSubmit={onStart} className="rounded border bg-white p-4 space-y-4">
        {/* Target */}
        <fieldset className="space-y-2">
          <legend className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
            Target
          </legend>
          <div className="flex gap-4">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="mode"
                value="address"
                checked={mode === "address"}
                onChange={() => setMode("address")}
              />
              By Address
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="mode"
                value="txHash"
                checked={mode === "txHash"}
                onChange={() => setMode("txHash")}
              />
              By Tx Hash
            </label>
          </div>
          <input
            type="text"
            className="w-full border rounded px-3 py-2 font-mono text-sm"
            placeholder={mode === "address" ? "anim1… or 0x…" : "0x… (transaction hash)"}
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            disabled={busy}
            spellCheck={false}
          />
        </fieldset>

        {/* Files */}
        <fieldset className="space-y-3">
          <legend className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
            Inputs
          </legend>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <FileRow
              label="Source (.py)"
              accept=".py"
              file={sourceFile}
              onFile={setSourceFile}
              digest={localHashes.source}
              disabled={busy}
            />
            <FileRow
              label="Manifest (JSON)"
              accept=".json,application/json"
              file={manifestFile}
              onFile={setManifestFile}
              digest={localHashes.manifest}
              disabled={busy}
            />
            <FileRow
              label="ABI (JSON, optional)"
              accept=".json,application/json"
              file={abiFile}
              onFile={setAbiFile}
              digest={localHashes.abi}
              disabled={busy}
              optional
            />
          </div>
        </fieldset>

        {/* Actions */}
        <div className="flex items-center gap-3">
          <button
            type="submit"
            className="px-3 py-2 rounded bg-blue-600 text-white text-sm disabled:opacity-50"
            disabled={busy}
          >
            {busy ? "Verifying…" : "Start Verification"}
          </button>
          {(status === "succeeded" || status === "failed") && (
            <button
              type="button"
              className="px-3 py-2 rounded border text-sm"
              onClick={onReset}
              disabled={busy}
            >
              Reset
            </button>
          )}
          {error && <span className="text-sm text-red-600 break-all">{error}</span>}
        </div>
      </form>

      {/* Live status */}
      {status !== "idle" && (
        <section className="rounded border bg-white p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
              Job Status
            </div>
            {jobId && (
              <code className="text-[11px] px-2 py-1 rounded bg-[color:var(--muted-bg,#f3f4f6)]">
                {jobId}
              </code>
            )}
          </div>
          <Stepper status={status} />
        </section>
      )}

      {/* Result */}
      {result && (
        <section className="rounded border bg-white p-4 space-y-3">
          <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
            Result
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <KV label="Target" value={`${result.targetType}:${shorten(result.target)}`} />
            <KV label="Match" value={result.match ? "Yes" : "No"} />
            <KV label="Compiled Code Hash" value={shorten(result.compiledCodeHash)} mono />
            <KV
              label="On-chain Code Hash"
              value={result.onChainCodeHash ? shorten(result.onChainCodeHash) : "—"}
              mono
            />
            {result.manifestDigest && (
              <KV label="Manifest Digest" value={shorten(result.manifestDigest)} mono />
            )}
            {localHashes.source && <KV label="Source Digest (local)" value={shorten(localHashes.source)} mono />}
          </div>

          {result.diagnostics && result.diagnostics.length > 0 && (
            <details className="mt-2">
              <summary className="cursor-pointer text-sm">Diagnostics</summary>
              <ul className="mt-2 text-sm list-disc pl-5">
                {result.diagnostics.map((d, i) => (
                  <li key={i} className={d.level === "error" ? "text-red-600" : d.level === "warning" ? "text-yellow-700" : ""}>
                    [{d.level}] {d.msg}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <div className="pt-2">
            <button
              className="px-3 py-2 rounded border text-sm"
              onClick={() => downloadJson(result, "verify-result.json")}
            >
              Download JSON
            </button>
          </div>
        </section>
      )}
    </div>
  );
}

/* --------------------------------- UI ----------------------------------- */

function FileRow({
  label,
  accept,
  file,
  onFile,
  digest,
  disabled,
  optional,
}: {
  label: string;
  accept?: string;
  file: File | null;
  onFile: (f: File | null) => void;
  digest?: string;
  disabled?: boolean;
  optional?: boolean;
}) {
  const id = React.useId();
  return (
    <div>
      <label htmlFor={id} className="block text-sm mb-1">
        {label} {optional ? <span className="text-[color:var(--muted,#6b7280)]">(optional)</span> : null}
      </label>
      <div className="flex items-center gap-3">
        <input
          id={id}
          type="file"
          accept={accept}
          onChange={(e) => onFile(e.target.files?.[0] || null)}
          disabled={disabled}
          className="block w-full text-sm"
        />
        {file && (
          <button
            type="button"
            className="px-2 py-1 text-xs rounded border"
            onClick={() => onFile(null)}
            disabled={disabled}
            title="Remove file"
          >
            Clear
          </button>
        )}
      </div>
      {file && (
        <div className="mt-1 text-xs text-[color:var(--muted,#6b7280)] break-all">
          {file.name} {digest ? <span>— digest {shorten(digest)}</span> : null}
        </div>
      )}
    </div>
  );
}

function KV({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-start gap-2">
      <div className="min-w-[160px] text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
        {label}
      </div>
      <div className={["flex-1 text-sm break-all", mono ? "font-mono" : ""].join(" ")}>{value}</div>
    </div>
  );
}

function Stepper({ status }: { status: VerifyStatus }) {
  const order: Array<[VerifyStatus, string]> = [
    ["queued", "Queued"],
    ["running", "Compiling/Comparing"],
    ["succeeded", "Done"],
  ];
  const idx = indexFor(status);
  return (
    <ol className="flex flex-wrap gap-3">
      {order.map(([st, label], i) => {
        const done = i < idx || status === "succeeded";
        const active = st === status && status !== "succeeded" && status !== "failed";
        return (
          <li key={st} className="flex items-center gap-2">
            <div
              className={[
                "w-5 h-5 rounded-full border flex items-center justify-center text-[10px]",
                done
                  ? "bg-green-600 border-green-600 text-white"
                  : active
                  ? "bg-blue-600 border-blue-600 text-white"
                  : "bg-white border-[color:var(--divider,#e5e7eb)] text-[color:var(--muted,#6b7280)]",
              ].join(" ")}
            >
              {done ? "✓" : i + 1}
            </div>
            <span
              className={[
                "text-sm",
                done || active ? "text-[color:var(--fg,#111827)]" : "text-[color:var(--muted,#6b7280)]",
              ].join(" ")}
            >
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function indexFor(s: VerifyStatus) {
  switch (s) {
    case "queued":
      return 0;
    case "running":
      return 1;
    case "succeeded":
      return 2;
    case "failed":
      return 1;
    default:
      return 0;
  }
}

/* ------------------------------- Helpers -------------------------------- */

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

async function readText(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onerror = () => reject(new Error("Failed to read file"));
    fr.onload = () => resolve(String(fr.result || ""));
    fr.readAsText(f);
  });
}

async function readJson(f: File): Promise<any> {
  const txt = await readText(f);
  try {
    return JSON.parse(txt);
  } catch (e) {
    throw new Error(`Invalid JSON in "${f.name}"`);
  }
}

async function digestFile(f: File): Promise<string> {
  const buf = await f.arrayBuffer();
  const hash = await sha3_256(new Uint8Array(buf));
  return "0x" + bytesToHex(hash);
}

function shorten(v: string, head = 10, tail = 8) {
  if (!v) return "";
  if (v.length <= head + tail + 3) return v;
  return `${v.slice(0, head)}…${v.slice(-tail)}`;
}

/* --------------------------- servicesApi glue ---------------------------- */

/**
 * We depend on a lightweight verify client under servicesApi.verify.*.
 * To keep this page portable, we define a narrow adapter here that
 * delegates to ../../services/servicesApi when present.
 */

const servicesApi = {
  verify: {
    async submit(
      baseUrl: string,
      body: {
        targetType: TargetMode;
        target: string;
        source: string;
        manifest: any;
        abi?: any;
      }
    ): Promise<VerifyPostResponse> {
      const url = join(baseUrl, "/verify");
      const res = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`Verify submit failed (${res.status})`);
      return (await res.json()) as VerifyPostResponse;
    },

    async status(baseUrl: string, jobId: string): Promise<VerifyStatusResponse> {
      const url = join(baseUrl, `/verify/status/${encodeURIComponent(jobId)}`);
      const res = await fetch(url, { method: "GET" });
      if (!res.ok) throw new Error(`Verify status failed (${res.status})`);
      return (await res.json()) as VerifyStatusResponse;
    },
  },
};

function join(base: string, path: string) {
  const b = (base || "").replace(/\/+$/, "");
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${b}${p}`;
}
