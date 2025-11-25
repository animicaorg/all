import React, { useCallback, useMemo, useState } from "react";

// Simple ABI viewer & client stub generator
// - Paste/upload ABI JSON
// - Preview normalized ABI (functions/events)
// - Generate client stubs for TS / Python / Rust
// This page is self-contained and does not require network/services.

type AbiParam = {
  name?: string;
  type: string;
  components?: AbiParam[];
  indexed?: boolean;
};

type AbiItem =
  | {
      type: "function";
      name: string;
      stateMutability?: "view" | "pure" | "nonpayable" | "payable" | string;
      inputs?: AbiParam[];
      outputs?: AbiParam[];
    }
  | {
      type: "event";
      name: string;
      anonymous?: boolean;
      inputs?: AbiParam[];
    }
  | {
      type: "constructor";
      stateMutability?: "nonpayable" | "payable" | string;
      inputs?: AbiParam[];
    }
  | {
      type: "fallback" | "receive";
      stateMutability?: string;
    };

type AbiLike =
  | AbiItem[]
  | {
      abi: AbiItem[];
      contractName?: string;
      name?: string;
    };

type Lang = "ts" | "py" | "rs";

const DEFAULT_CLASS = "MyContract";
const BTN = {
  base: {
    padding: "8px 12px",
    borderRadius: 8,
    border: "1px solid var(--border,#e5e7eb)",
    background: "var(--btn-bg,#fff)",
    cursor: "pointer",
    fontWeight: 600,
    fontSize: 13,
  } as React.CSSProperties,
  ghost: {
    padding: "6px 10px",
    borderRadius: 6,
    border: "1px solid var(--border,#e5e7eb)",
    background: "transparent",
    cursor: "pointer",
    fontWeight: 600,
    fontSize: 12,
  } as React.CSSProperties,
};

// ---------------- ABI parse / normalize ----------------

function parseAbi(raw: string): { name: string; abi: AbiItem[]; errors: string[] } {
  const errors: string[] = [];
  let obj: any;
  try {
    obj = JSON.parse(raw);
  } catch (e: any) {
    return { name: DEFAULT_CLASS, abi: [], errors: ["Invalid JSON", e?.message || String(e)] };
  }
  if (Array.isArray(obj)) {
    const name = DEFAULT_CLASS;
    const abi = obj.filter(isAbiItem) as AbiItem[];
    if (abi.length === 0) errors.push("No ABI entries found.");
    return { name, abi, errors };
  }
  if (typeof obj === "object" && obj) {
    const name = obj.contractName || obj.name || DEFAULT_CLASS;
    const abi = Array.isArray(obj.abi) ? (obj.abi.filter(isAbiItem) as AbiItem[]) : [];
    if (abi.length === 0) errors.push("No ABI entries found at object.abi[]");
    return { name, abi, errors };
  }
  return { name: DEFAULT_CLASS, abi: [], errors: ["Unsupported ABI shape"] };
}

function isAbiItem(x: any): x is AbiItem {
  if (!x || typeof x !== "object") return false;
  if (x.type === "function" && typeof x.name === "string") return true;
  if (x.type === "event" && typeof x.name === "string") return true;
  if (x.type === "constructor" || x.type === "fallback" || x.type === "receive") return true;
  return false;
}

function isViewLike(fn: AbiItem & { type: "function" }): boolean {
  const m = (fn.stateMutability || "").toLowerCase();
  return m === "view" || m === "pure";
}

// ---------------- Minimal type mappers for codegen ----------------

function mapTs(t: string): string {
  const base = t.replace(/\s+/g, "");
  if (base.endsWith("]")) return "any[]";
  if (base.startsWith("uint") || base.startsWith("int")) return "bigint";
  if (base === "bool") return "boolean";
  if (base === "string") return "string";
  if (base === "address") return "string";
  if (base.startsWith("bytes")) return `\`0x\${string}\``;
  return "any";
}
function mapPy(t: string): string {
  const base = t.replace(/\s+/g, "");
  if (base.endsWith("]")) return "list";
  if (base.startsWith("uint") || base.startsWith("int")) return "int";
  if (base === "bool") return "bool";
  if (base === "string") return "str";
  if (base === "address") return "str";
  if (base.startsWith("bytes")) return "bytes";
  return "typing.Any";
}
function mapRs(t: string): string {
  const base = t.replace(/\s+/g, "");
  if (/\[[^\]]+\]$/.test(base)) return "Vec<Vec<u8>>";
  if (base.startsWith("uint") || base.startsWith("int")) return "u128";
  if (base === "bool") return "bool";
  if (base === "string") return "String";
  if (base === "address") return "String";
  if (base.startsWith("bytes")) return "Vec<u8>";
  return "serde_json::Value";
}

function paramName(i: number, p?: AbiParam) {
  const raw = (p?.name || "").trim();
  if (raw) return sanitizeIdent(raw);
  return `arg${i}`;
}
function sanitizeIdent(s: string): string {
  return s.replace(/[^a-zA-Z0-9_]/g, "_").replace(/^(\d)/, "_$1");
}

// ---------------- Generators ----------------

function genTs(name: string, abi: AbiItem[]): string {
  const className = sanitizeIdent(capitalize(name)) + "Client";
  const fns = abi.filter((x): x is Extract<AbiItem, { type: "function" }> => x.type === "function");

  const methods = fns
    .map((fn) => {
      const argsSig = (fn.inputs || [])
        .map((p, i) => `${paramName(i, p)}: ${mapTs(p.type)}`)
        .join(", ");
      const argArray = (fn.inputs || []).map((p, i) => paramName(i, p)).join(", ");
      const returns =
        (fn.outputs || []).length === 1 ? mapTs(fn.outputs![0].type) : (fn.outputs || []).length > 1 ? "any[]" : "void";
      if (isViewLike(fn)) {
        return `  /** view: ${fn.name} */\n  async ${sanitizeIdent(fn.name)}(${argsSig}): Promise<${returns}> {\n    return this.read(${JSON.stringify(
          fn.name
        )}, [${argArray}]);\n  }`;
      }
      return `  /** write: ${fn.name} */\n  async ${sanitizeIdent(
        fn.name
      )}(${argsSig}, opts?: { value?: bigint; gas?: bigint }): Promise<import("@animica/sdk").Receipt> {\n    return this.write(${JSON.stringify(
        fn.name
      )}, [${argArray}], opts);\n  }`;
    })
    .join("\n\n");

  return `// Auto-generated preview. Works with @animica/sdk ContractClient.
import { ContractClient, type Abi } from "@animica/sdk";

export class ${className} extends ContractClient {
  static readonly abi: Abi = ${JSON.stringify(abi, null, 2)};
  constructor(args: { rpc: import("@animica/sdk").RpcClient; address: string }) {
    super({ rpc: args.rpc, address: args.address, abi: ${className}.abi });
  }

${methods || "  // (no public functions in ABI)"}
}

// Usage:
// const c = new ${className}({ rpc, address });
// const x = await c.someView(arg0);
// const r = await c.someWrite(arg0, { gas: 200000n });
`;
}

function genPy(name: string, abi: AbiItem[]): string {
  const className = sanitizeIdent(capitalize(name));
  const fns = abi.filter((x): x is Extract<AbiItem, { type: "function" }> => x.type === "function");

  const methods = fns
    .map((fn) => {
      const argsSig = (fn.inputs || [])
        .map((p, i) => `${paramName(i, p)}: ${mapPy(p.type)}`)
        .join(", ");
      const argArray = (fn.inputs || []).map((p, i) => paramName(i, p)).join(", ");
      if (isViewLike(fn)) {
        return `    def ${sanitizeIdent(fn.name)}(self, ${argsSig}) -> typing.Any:
        """
        view: ${fn.name}
        """
        return self.client.read("${fn.name}", [${argArray}])`;
      }
      return `    def ${sanitizeIdent(fn.name)}(self, ${argsSig}, *, value: int | None = None, gas: int | None = None) -> dict:
        """
        write: ${fn.name}
        """
        return self.client.write("${fn.name}", [${argArray}], value=value, gas=gas)`;
    })
    .join("\n\n");

  return `# Auto-generated preview. Works with omni_sdk.contracts.client.ContractClient
from __future__ import annotations
import typing
from omni_sdk.contracts.client import ContractClient

ABI: list[dict] = ${JSON.stringify(abi, null, 2)}

class ${className}Client:
    def __init__(self, client: ContractClient) -> None:
        self.client = client.with_abi(ABI)

${methods || "    # (no public functions in ABI)"}

# Usage:
# from omni_sdk.contracts.client import ContractClient
# cc = ContractClient(rpc).at(address).with_abi(ABI)
# c = ${className}Client(cc)
# out = c.some_view(x)
# receipt = c.some_write(x, gas=200000)
`;
}

function genRs(name: string, abi: AbiItem[]): string {
  const structName = sanitizeIdent(capitalize(name)) + "Client";
  const fns = abi.filter((x): x is Extract<AbiItem, { type: "function" }> => x.type === "function");

  const methods = fns
    .map((fn) => {
      const argsSig = (fn.inputs || [])
        .map((p, i) => `${paramName(i, p)}: ${mapRs(p.type)}`)
        .join(", ");
      const argArray = (fn.inputs || []).map((p, i) => paramName(i, p)).join(", ");
      if (isViewLike(fn)) {
        return `    /// view: ${fn.name}
    pub async fn ${sanitizeIdent(fn.name)}(&self, ${argsSig}) -> anyhow::Result<serde_json::Value> {
        self.inner.read("${fn.name}", vec![${argArray}]).await
    }`;
      }
      return `    /// write: ${fn.name}
    pub async fn ${sanitizeIdent(
        fn.name
      )}(&self, ${argsSig}, gas: Option<u64>, value: Option<u128>) -> anyhow::Result<animica_sdk::types::Receipt> {
        self.inner.write("${fn.name}", vec![${argArray}], gas, value).await
    }`;
    })
    .join("\n\n");

  return `// Auto-generated preview. Works with animica_sdk contracts client shape.
use serde_json::json;

pub struct ${structName} {
    inner: animica_sdk::contracts::Client,
}

impl ${structName} {
    pub fn new(inner: animica_sdk::contracts::Client) -> Self {
        let abi: serde_json::Value = ${JSON.stringify(abi, null, 2).replace(/\n/g, "\n        ")};
        Self { inner: inner.with_abi(abi) }
    }

${methods || "    // (no public functions in ABI)"}
}

// Usage:
//
// let cc = animica_sdk::contracts::Client::new(rpc).at(address).with_abi(abi_json);
// let c = ${structName}::new(cc);
// let x = c.some_view(arg0).await?;
// let r = c.some_write(arg0, Some(200000), None).await?;
`;
}

// ---------------- UI helpers ----------------

function capitalize(s: string) {
  if (!s) return s;
  return s[0].toUpperCase() + s.slice(1);
}

function download(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function copyToClipboard(text: string) {
  return navigator.clipboard?.writeText(text);
}

// ---------------- Component ----------------

const examples = {
  counter: `{
  "contractName": "Counter",
  "abi": [
    {"type":"function","name":"get","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint256"}]},
    {"type":"function","name":"inc","stateMutability":"nonpayable","inputs":[{"name":"delta","type":"uint256"}],"outputs":[]},
    {"type":"event","name":"Incremented","inputs":[{"name":"by","type":"uint256","indexed":false}]}
  ]
}`,
};

const TabBtn: React.FC<{ active: boolean; onClick(): void; children: React.ReactNode; title?: string }> = ({
  active,
  onClick,
  children,
  title,
}) => (
  <button
    title={title}
    onClick={onClick}
    style={{
      ...BTN.ghost,
      background: active ? "var(--tab-active,#eef2ff)" : "transparent",
      borderColor: active ? "#c7d2fe" : "var(--border,#e5e7eb)",
    }}
  >
    {children}
  </button>
);

export const AbiViewerPage: React.FC = () => {
  const [raw, setRaw] = useState<string>(examples.counter);
  const [lang, setLang] = useState<Lang>("ts");
  const [contractNameOverride, setContractNameOverride] = useState<string>("");

  const parsed = useMemo(() => parseAbi(raw), [raw]);
  const name = (contractNameOverride || parsed.name || DEFAULT_CLASS).trim() || DEFAULT_CLASS;

  const code = useMemo(() => {
    if (!parsed.abi.length) return "// Paste a valid ABI to generate client code.";
    if (lang === "ts") return genTs(name, parsed.abi);
    if (lang === "py") return genPy(name, parsed.abi);
    return genRs(name, parsed.abi);
  }, [lang, name, parsed.abi]);

  const onUpload = useCallback(async (file: File) => {
    const text = await file.text();
    setRaw(text);
  }, []);

  return (
    <div style={{ padding: 16, display: "grid", gap: 16 }}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>ABI Viewer & Client Codegen (Preview)</h1>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <TabBtn active={lang === "ts"} onClick={() => setLang("ts")} title="TypeScript">
            TypeScript
          </TabBtn>
          <TabBtn active={lang === "py"} onClick={() => setLang("py")} title="Python">
            Python
          </TabBtn>
          <TabBtn active={lang === "rs"} onClick={() => setLang("rs")} title="Rust">
            Rust
          </TabBtn>
        </div>
      </header>

      <section
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(320px, 1fr) minmax(420px, 1.2fr)",
          gap: 16,
          alignItems: "start",
        }}
      >
        {/* Left: ABI input + summary */}
        <div
          style={{
            border: "1px solid var(--card-border,#e5e7eb)",
            borderRadius: 12,
            background: "var(--card-bg,#fff)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              padding: 12,
              borderBottom: "1px solid var(--card-border,#e5e7eb)",
              background: "var(--card-head,#f9fafb)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: 8,
            }}
          >
            <div style={{ fontWeight: 700, fontSize: 14 }}>ABI JSON</div>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                style={BTN.ghost}
                onClick={() => setRaw((r) => {
                  try { return JSON.stringify(JSON.parse(r), null, 2); } catch { return r; }
                })}
                title="Pretty-print"
              >
                Format
              </button>
              <label style={{ ...BTN.ghost, cursor: "pointer" }}>
                Upload
                <input
                  type="file"
                  accept=".json,.abi,application/json"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) onUpload(f);
                    e.currentTarget.value = "";
                  }}
                  style={{ display: "none" }}
                />
              </label>
            </div>
          </div>
          <textarea
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            spellCheck={false}
            placeholder='[{ "type": "function", "name": "foo", "inputs": [], "outputs": [] }]'
            style={{
              width: "100%",
              height: 280,
              padding: 12,
              border: "none",
              outline: "none",
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
              fontSize: 12.5,
              resize: "vertical",
              boxSizing: "border-box",
            }}
          />
          <div style={{ padding: 12, borderTop: "1px solid var(--card-border,#e5e7eb)", display: "grid", gap: 10 }}>
            <div style={{ display: "grid", gridTemplateColumns: "140px 1fr", gap: 10, alignItems: "center" }}>
              <div style={{ color: "var(--muted-fg,#667085)", fontSize: 12, fontWeight: 700 }}>Class name</div>
              <input
                placeholder={parsed.name || DEFAULT_CLASS}
                value={contractNameOverride}
                onChange={(e) => setContractNameOverride(e.target.value)}
                style={{
                  padding: "8px 10px",
                  borderRadius: 8,
                  border: "1px solid var(--border,#e5e7eb)",
                  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                }}
              />
            </div>

            {parsed.errors.length > 0 && (
              <div
                style={{
                  padding: 10,
                  borderRadius: 8,
                  border: "1px solid #fecaca",
                  background: "#fef2f2",
                  color: "#991b1b",
                  fontSize: 12.5,
                }}
              >
                {parsed.errors.map((e, i) => (
                  <div key={i}>• {e}</div>
                ))}
              </div>
            )}

            <div style={{ display: "grid", gap: 6 }}>
              <div style={{ fontWeight: 700, fontSize: 13 }}>Summary</div>
              <div style={{ color: "var(--muted-fg,#667085)", fontSize: 12 }}>
                {parsed.abi.length} ABI entries ·{" "}
                {parsed.abi.filter((x) => x.type === "function").length} functions ·{" "}
                {parsed.abi.filter((x) => x.type === "event").length} events
              </div>
              <div style={{ maxHeight: 180, overflow: "auto", borderTop: "1px solid var(--row,#eee)" }}>
                {parsed.abi
                  .filter((x): x is Extract<AbiItem, { type: "function" | "event" }> => x.type === "function" || x.type === "event")
                  .map((it, i) => (
                    <div
                      key={`${it.type}-${(it as any).name}-${i}`}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "80px 1fr",
                        gap: 10,
                        padding: "8px 0",
                        borderTop: i === 0 ? "none" : "1px solid var(--row,#eee)",
                      }}
                    >
                      <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: "#6b7280" }}>
                        {it.type}
                      </div>
                      <div style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12.5 }}>
                        {(it as any).name}
                        {"("}
                        {((it as any).inputs || [])
                          .map((p: AbiParam) => `${p.name || ""}:${p.type}`)
                          .join(", ")}
                        {")"}
                        {" "}
                        {it.type === "function" && (it as any).stateMutability
                          ? `— ${(it as any).stateMutability}`
                          : ""}
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          </div>
        </div>

        {/* Right: Code preview */}
        <div
          style={{
            border: "1px solid var(--card-border,#e5e7eb)",
            borderRadius: 12,
            background: "var(--card-bg,#fff)",
            overflow: "hidden",
            display: "grid",
            gridTemplateRows: "auto 1fr auto",
          }}
        >
          <div
            style={{
              padding: 12,
              borderBottom: "1px solid var(--card-border,#e5e7eb)",
              background: "var(--card-head,#f9fafb)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 8,
            }}
          >
            <div style={{ fontWeight: 700, fontSize: 14 }}>
              Generated {lang.toUpperCase()} client preview
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                style={BTN.ghost}
                onClick={() => {
                  const filename =
                    lang === "ts"
                      ? `${sanitizeIdent(name)}Client.ts`
                      : lang === "py"
                      ? `${sanitizeIdent(name)}_client.py`
                      : `${sanitizeIdent(name)}_client.rs`;
                  download(filename, code);
                }}
              >
                Download
              </button>
              <button
                style={BTN.ghost}
                onClick={() => copyToClipboard(code)}
                title="Copy to clipboard"
              >
                Copy
              </button>
            </div>
          </div>
          <div style={{ overflow: "auto" }}>
            <pre
              style={{
                margin: 0,
                padding: 12,
                fontSize: 12.5,
                lineHeight: 1.45,
                tabSize: 2,
                background: "var(--code-bg,#0b1020)",
                color: "var(--code-fg,#e6edf3)",
                minHeight: 300,
              }}
            >
              <code>{code}</code>
            </pre>
          </div>
          <div style={{ padding: 10, borderTop: "1px solid var(--card-border,#e5e7eb)", fontSize: 12, color: "#6b7280" }}>
            Preview only. For production, consider using the dedicated codegen in this repo (Python/TS/Rust) to ensure full type coverage.
          </div>
        </div>
      </section>
    </div>
  );
};

export default AbiViewerPage;
