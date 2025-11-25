import * as React from "react";
import { useProjectStore } from "../../../state/project";
import { useCompileStore } from "../../../state/compile";
import { bytesToHex } from "../../../utils/bytes";
import { downloadText } from "../../../utils/download";
import { sha3_512_hex } from "../../../utils/hash";

/**
 * ArtifactsPanel
 * - Shows code hash (sha3-512), size, manifest validity
 * - Previews a normalized artifact (manifest + code hash + metadata)
 * - Copy/Download helpers
 *
 * This panel is resilient to partial state: it will compute a code hash from
 * whichever code representation is available (compiled IR bytes preferred,
 * otherwise source text).
 */

type Manifest = {
  name?: string;
  version?: string;
  abi?: any;
  [k: string]: any;
};

export default function ArtifactsPanel() {
  const project = useProjectStore((s: any) => s);
  const compile = useCompileStore((s: any) => s);

  // Resolve code bytes: prefer compiled IR/code bytes, fallback to current source.
  const codeBytes: Uint8Array | null = React.useMemo(() => {
    const b =
      (compile?.irBytes as Uint8Array | undefined) ||
      (compile?.codeBytes as Uint8Array | undefined);
    if (b && b.byteLength) return b;
    const src: string | undefined =
      compile?.source ??
      project?.activeFile?.content ??
      tryPickSourceFromProject(project);
    return src ? new TextEncoder().encode(src) : null;
  }, [compile?.irBytes, compile?.codeBytes, compile?.source, project?.activeFile?.content]);

  // Compute sha3-512 code hash (hex, 0x-prefixed).
  const [codeHash, setCodeHash] = React.useState<string | null>(null);
  React.useEffect(() => {
    let closed = false;
    (async () => {
      try {
        if (!codeBytes || !codeBytes.byteLength) {
          if (!closed) setCodeHash(null);
          return;
        }
        const hex = await sha3_512_hex(codeBytes);
        if (!closed) setCodeHash("0x" + hex);
      } catch {
        if (!closed) setCodeHash(null);
      }
    })();
    return () => {
      closed = true;
    };
  }, [codeBytes]);

  // Parse manifest from compile state or project files.
  const { manifest, manifestError } = React.useMemo(() => {
    // prefer compiled/selected manifest object if provided by compiler hook
    const mFromCompile = compile?.manifest as Manifest | undefined;
    if (mFromCompile && typeof mFromCompile === "object") {
      return { manifest: mFromCompile as Manifest, manifestError: null as string | null };
    }
    // fallback: find a manifest.json in project tree
    const raw = tryPickManifestJson(project);
    if (!raw) return { manifest: null as Manifest | null, manifestError: "No manifest found" };
    try {
      const obj = JSON.parse(raw);
      return { manifest: obj as Manifest, manifestError: null as string | null };
    } catch (e: any) {
      return { manifest: null as Manifest | null, manifestError: String(e?.message || e) };
    }
  }, [compile?.manifest, project?.files, project?.activeFile]);

  // Build normalized artifact preview.
  const artifact = React.useMemo(() => {
    const now = new Date().toISOString();
    return {
      meta: {
        createdAt: now,
        tool: "studio-web",
      },
      code: {
        hash: codeHash,
        size: codeBytes?.byteLength ?? 0,
        // DO NOT embed actual code by default to keep artifact small. Can be toggled below.
        // bytesHex: codeBytes ? "0x" + bytesToHex(codeBytes) : undefined,
      },
      manifest: manifest ?? undefined,
      abi: manifest?.abi ?? compile?.abi ?? undefined,
    };
  }, [codeHash, codeBytes, manifest, compile?.abi]);

  const copyHash = async () => {
    if (!codeHash) return;
    try {
      await navigator.clipboard.writeText(codeHash);
      compile?.toast?.("Copied code hash");
    } catch {
      // swallow
    }
  };

  const copyArtifact = async () => {
    try {
      await navigator.clipboard.writeText(safeStringify(artifact, 2));
      compile?.toast?.("Copied artifact JSON");
    } catch {
      // swallow
    }
  };

  const downloadArtifact = () => {
    downloadText("artifact.json", safeStringify(artifact, 2));
  };

  const [includeCodeBytes, setIncludeCodeBytes] = React.useState(false);
  const artifactWithBytes = React.useMemo(() => {
    if (!includeCodeBytes || !codeBytes) return artifact;
    return {
      ...artifact,
      code: {
        ...artifact.code,
        bytesHex: "0x" + bytesToHex(codeBytes),
      },
    };
  }, [includeCodeBytes, codeBytes, artifact]);

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="p-3 border-b border-[color:var(--divider,#e5e7eb)]">
        <div className="flex flex-wrap items-center gap-2">
          <div className="text-sm font-semibold">Artifact Preview</div>
          <span className="ml-auto text-xs text-[color:var(--muted,#6b7280)]">
            {codeBytes ? `${codeBytes.byteLength} bytes` : "no code"}
          </span>
        </div>
      </div>

      {/* Summary cards */}
      <div className="p-3 grid grid-cols-1 md:grid-cols-3 gap-3">
        <Card title="Code Hash (sha3-512)">
          <div className="flex items-center gap-2">
            <code className="text-xs break-all">
              {codeHash ?? "—"}
            </code>
            <button
              type="button"
              onClick={copyHash}
              disabled={!codeHash}
              className="ml-auto px-2 py-1 text-xs rounded border border-[color:var(--divider,#e5e7eb)] bg-[color:var(--panel-bg,#f9fafb)] hover:bg-white disabled:opacity-60"
            >
              Copy
            </button>
          </div>
        </Card>

        <Card title="Manifest">
          <div className="text-sm">
            {manifest ? (
              <div className="space-y-1">
                <Row label="name">{manifest?.name ?? <em>—</em>}</Row>
                <Row label="version">{manifest?.version ?? <em>—</em>}</Row>
                <Row label="abi">
                  {Array.isArray(manifest?.abi) ? (
                    <span>{manifest!.abi.length} entries</span>
                  ) : (
                    <em>missing</em>
                  )}
                </Row>
              </div>
            ) : (
              <div className="text-[color:var(--muted,#6b7280)]">
                {manifestError || "No manifest detected"}
              </div>
            )}
          </div>
        </Card>

        <Card title="Actions">
          <div className="flex flex-wrap items-center gap-2">
            <label className="inline-flex items-center gap-2 text-xs text-[color:var(--muted,#6b7280)]">
              <input
                type="checkbox"
                className="accent-[color:var(--accent,#0284c7)]"
                checked={includeCodeBytes}
                onChange={(e) => setIncludeCodeBytes(e.target.checked)}
              />
              Include code bytes (hex)
            </label>
            <button
              type="button"
              onClick={copyArtifact}
              className="px-2 py-1 text-xs rounded border border-[color:var(--divider,#e5e7eb)] bg-[color:var(--panel-bg,#f9fafb)] hover:bg-white"
            >
              Copy JSON
            </button>
            <button
              type="button"
              onClick={downloadArtifact}
              className="px-2 py-1 text-xs rounded border border-[color:var(--divider,#e5e7eb)] bg-[color:var(--panel-bg,#f9fafb)] hover:bg-white"
            >
              Download
            </button>
          </div>
        </Card>
      </div>

      {/* JSON Preview */}
      <div className="flex-1 overflow-auto p-3">
        <div className="rounded border border-[color:var(--divider,#e5e7eb)] bg-white">
          <div className="px-3 py-2 border-b border-[color:var(--divider,#e5e7eb)] text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
            artifact.json (preview)
          </div>
          <pre className="p-3 text-[0.8rem] whitespace-pre-wrap break-words">
            {safeStringify(artifactWithBytes, 2)}
          </pre>
        </div>
      </div>
    </div>
  );
}

/* ----------------------------- helpers & UI ------------------------------ */

function tryPickManifestJson(project: any): string | null {
  // common paths
  const candidates = [
    "manifest.json",
    "examples/counter/manifest.json",
    "src/manifest.json",
    "contracts/manifest.json",
  ];

  // project.files may be a Map-like {path -> content} or array of {path, content}
  const files = project?.files;
  if (!files) return null;

  // Record<string,string> shape
  if (files && typeof files === "object" && !Array.isArray(files)) {
    for (const p of Object.keys(files)) {
      if (p.toLowerCase().endsWith("manifest.json") || candidates.includes(p)) {
        const v = (files as Record<string, string>)[p];
        if (typeof v === "string") return v;
      }
    }
  }

  // Array of { path, content }
  if (Array.isArray(files)) {
    for (const f of files) {
      const p = (f?.path || "").toString();
      if (p.toLowerCase().endsWith("manifest.json") || candidates.includes(p)) {
        const v = f?.content;
        if (typeof v === "string") return v;
      }
    }
  }

  // Maybe activeFile is the manifest
  const active = project?.activeFile;
  if (
    active?.path &&
    String(active.path).toLowerCase().endsWith("manifest.json") &&
    typeof active.content === "string"
  ) {
    return active.content;
  }

  return null;
}

function tryPickSourceFromProject(project: any): string | null {
  // A few likely locations for the sample templates.
  const candidates = [
    "contract.py",
    "examples/counter/contract.py",
    "contracts/contract.py",
    "src/contract.py",
  ];

  const files = project?.files;
  if (!files) return null;

  if (files && typeof files === "object" && !Array.isArray(files)) {
    for (const p of Object.keys(files)) {
      if (candidates.some((c) => p.endsWith(c))) {
        const v = (files as Record<string, string>)[p];
        if (typeof v === "string") return v;
      }
    }
  }

  if (Array.isArray(files)) {
    for (const f of files) {
      const p = (f?.path || "").toString();
      if (candidates.some((c) => p.endsWith(c))) {
        const v = f?.content;
        if (typeof v === "string") return v;
      }
    }
  }

  const active = project?.activeFile;
  if (active?.content && typeof active.content === "string") {
    return active.content;
  }

  return null;
}

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

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-12 gap-2">
      <div className="col-span-4 sm:col-span-3 text-[0.7rem] uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
        {label}
      </div>
      <div className="col-span-8 sm:col-span-9 text-sm">{children}</div>
    </div>
  );
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
