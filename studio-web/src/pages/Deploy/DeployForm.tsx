import * as React from "react";

/**
 * DeployForm
 * A reusable, controlled form component for providing:
 *  - Contract manifest JSON (may include ABI)
 *  - Optional standalone ABI JSON (used if manifest lacks abi)
 *  - Code bytes (hex or file)
 *  - Value & Nonce
 *  - Fee configuration (auto / legacy / EIP-1559-like)
 *
 * This component is presentation-only; it does not call SDKs or the network.
 * Host pages should own state and pass {values, onChange}. See types below.
 */

/* --------------------------------- Types -------------------------------- */

export type FeeMode = "auto" | "legacy" | "eip1559";

export interface DeployFormValues {
  manifestText: string;
  abiText: string; // optional, overrides manifest.abi if present
  codeHex: string;
  value: string; // decimal or 0x-hex
  nonce: string; // decimal (optional)
  feeMode: FeeMode;

  // Legacy
  gasLimit: string; // decimal
  gasPrice: string; // decimal or 0x-hex

  // EIP-1559-like
  maxFeePerGas: string; // decimal or 0x-hex
  maxPriorityFeePerGas: string; // decimal or 0x-hex
}

export interface DeployFormProps {
  values: DeployFormValues;
  onChange: (patch: Partial<DeployFormValues>) => void;
  onLoadExample?: () => void;
  errors?: Partial<Record<keyof DeployFormValues | "manifest" | "abi" | "code", string>>;
  disabled?: boolean;
}

export type FeeConfig =
  | { mode: "auto" }
  | { mode: "legacy"; gasLimit: bigint; gasPrice: bigint }
  | {
      mode: "eip1559";
      gasLimit: bigint;
      maxFeePerGas: bigint;
      maxPriorityFeePerGas: bigint;
    };

/* ------------------------------- Component ------------------------------ */

export default function DeployForm({
  values,
  onChange,
  onLoadExample,
  errors,
  disabled,
}: DeployFormProps) {
  const manifestFileRef = React.useRef<HTMLInputElement>(null);
  const abiFileRef = React.useRef<HTMLInputElement>(null);
  const codeFileRef = React.useRef<HTMLInputElement>(null);

  const manifestMeta = React.useMemo(() => summarizeManifest(values.manifestText), [values.manifestText]);
  const abiMeta = React.useMemo(() => summarizeAbi(values.abiText || extractAbi(values.manifestText)), [values.abiText, values.manifestText]);

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold">Contract Inputs</h3>
        <div className="ml-auto flex items-center gap-2">
          {onLoadExample && (
            <button
              type="button"
              className="px-3 py-1.5 text-xs rounded border bg-white"
              onClick={onLoadExample}
              disabled={disabled}
            >
              Load Example
            </button>
          )}
        </div>
      </div>

      {/* Manifest */}
      <Section title="Manifest (JSON)">
        <textarea
          className="w-full border rounded px-2 py-2 text-sm h-36"
          placeholder='{"name":"MyContract","version":"1.0.0","abi":{...}}'
          value={values.manifestText}
          onChange={(e) => onChange({ manifestText: e.target.value })}
          disabled={disabled}
        />
        <div className="flex items-center gap-2 mt-1">
          <input
            ref={manifestFileRef}
            type="file"
            accept=".json,application/json"
            onChange={(e) => pickAsText(e, (text) => onChange({ manifestText: text }))}
            disabled={disabled}
          />
          <button
            type="button"
            className="px-2 py-1 text-xs rounded border bg-white"
            onClick={() => {
              if (manifestFileRef.current) manifestFileRef.current.value = "";
              onChange({ manifestText: "" });
            }}
            disabled={disabled}
          >
            Clear
          </button>
        </div>
        <FieldError text={errors?.manifest} />
        <MetaRow label="Name" value={manifestMeta.name} />
        <MetaRow label="Version" value={manifestMeta.version} />
        <MetaRow label="ABI" value={manifestMeta.hasAbi ? "Present" : "Missing"} />
      </Section>

      {/* ABI (optional explicit) */}
      <Section title="ABI (JSON, optional — overrides manifest.abi)">
        <textarea
          className="w-full border rounded px-2 py-2 text-sm h-28"
          placeholder='{"functions":[...],"events":[...]}'
          value={values.abiText}
          onChange={(e) => onChange({ abiText: e.target.value })}
          disabled={disabled}
        />
        <div className="flex items-center gap-2 mt-1">
          <input
            ref={abiFileRef}
            type="file"
            accept=".json,application/json"
            onChange={(e) => pickAsText(e, (text) => onChange({ abiText: text }))}
            disabled={disabled}
          />
          <button
            type="button"
            className="px-2 py-1 text-xs rounded border bg-white"
            onClick={() => {
              if (abiFileRef.current) abiFileRef.current.value = "";
              onChange({ abiText: "" });
            }}
            disabled={disabled}
          >
            Clear
          </button>
        </div>
        <FieldError text={errors?.abi} />
        <MetaRow label="Functions" value={abiMeta.functions} />
        <MetaRow label="Events" value={abiMeta.events} />
      </Section>

      {/* Code */}
      <Section title="Code (hex or binary file)">
        <textarea
          className="w-full border rounded px-2 py-2 text-sm h-24"
          placeholder="0xdeadbeef..."
          value={values.codeHex}
          onChange={(e) => onChange({ codeHex: normalizeHex(e.target.value) })}
          disabled={disabled}
        />
        <div className="flex items-center gap-2 mt-1">
          <input
            ref={codeFileRef}
            type="file"
            onChange={(e) => pickCode(e, (hex) => onChange({ codeHex: hex }))}
            disabled={disabled}
          />
          <button
            type="button"
            className="px-2 py-1 text-xs rounded border bg-white"
            onClick={() => {
              if (codeFileRef.current) codeFileRef.current.value = "";
              onChange({ codeHex: "" });
            }}
            disabled={disabled}
          >
            Clear
          </button>
        </div>
        <FieldError text={errors?.code} />
        <MetaRow label="Code size" value={humanBytes(estimateHexSize(values.codeHex))} />
      </Section>

      {/* Value / Nonce */}
      <Section title="Value & Nonce">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <LabeledInput
            label="Value"
            placeholder="0"
            value={values.value}
            onChange={(v) => onChange({ value: v })}
            disabled={disabled}
          />
          <LabeledInput
            label="Nonce (optional)"
            placeholder=""
            value={values.nonce}
            onChange={(v) => onChange({ nonce: v })}
            disabled={disabled}
          />
        </div>
      </Section>

      {/* Fees */}
      <Section title="Fees">
        <div className="flex items-center gap-3">
          <label className="text-xs">Mode</label>
          <select
            className="border rounded px-2 py-1 text-sm"
            value={values.feeMode}
            onChange={(e) => onChange({ feeMode: e.target.value as FeeMode })}
            disabled={disabled}
          >
            <option value="auto">Auto</option>
            <option value="legacy">Legacy (gasPrice)</option>
            <option value="eip1559">EIP-1559 (maxFee/maxPrio)</option>
          </select>
        </div>

        {values.feeMode === "legacy" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-2">
            <LabeledInput
              label="Gas Limit"
              placeholder="e.g. 500000"
              value={values.gasLimit}
              onChange={(v) => onChange({ gasLimit: v })}
              disabled={disabled}
            />
            <LabeledInput
              label="Gas Price"
              placeholder="wei (decimal) or 0x-hex"
              value={values.gasPrice}
              onChange={(v) => onChange({ gasPrice: v })}
              disabled={disabled}
            />
          </div>
        )}

        {values.feeMode === "eip1559" && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-2">
            <LabeledInput
              label="Gas Limit"
              placeholder="e.g. 500000"
              value={values.gasLimit}
              onChange={(v) => onChange({ gasLimit: v })}
              disabled={disabled}
            />
            <LabeledInput
              label="Max Fee Per Gas"
              placeholder="decimal or 0x-hex"
              value={values.maxFeePerGas}
              onChange={(v) => onChange({ maxFeePerGas: v })}
              disabled={disabled}
            />
            <LabeledInput
              label="Max Priority Fee Per Gas"
              placeholder="decimal or 0x-hex"
              value={values.maxPriorityFeePerGas}
              onChange={(v) => onChange({ maxPriorityFeePerGas: v })}
              disabled={disabled}
            />
          </div>
        )}

        {values.feeMode === "auto" && (
          <div className="text-xs text-[color:var(--muted,#6b7280)] mt-2">
            Fees will be estimated automatically during build/estimate steps.
          </div>
        )}
      </Section>
    </div>
  );
}

/* --------------------------------- UI ----------------------------------- */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-[color:var(--divider,#e5e7eb)] bg-white p-3">
      <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">{title}</div>
      <div className="mt-2 space-y-2">{children}</div>
    </div>
  );
}

function LabeledInput({
  label,
  value,
  onChange,
  placeholder,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <label className="block">
      <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-1">{label}</div>
      <input
        className="w-full border rounded px-2 py-1 text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
      />
    </label>
  );
}

function MetaRow({ label, value }: { label: string; value: string | number | boolean | null | undefined }) {
  return (
    <div className="flex items-start gap-2 text-xs">
      <div className="min-w-[120px] text-[color:var(--muted,#6b7280)]">{label}</div>
      <div className="flex-1">{value === undefined || value === null || value === "" ? "—" : String(value)}</div>
    </div>
  );
}

function FieldError({ text }: { text?: string }) {
  if (!text) return null;
  return <div className="text-xs text-red-600">{text}</div>;
}

/* ------------------------------ Helpers --------------------------------- */

function pickAsText(e: React.ChangeEvent<HTMLInputElement>, cb: (text: string) => void) {
  const f = e.target.files?.[0];
  if (!f) return;
  f.text().then((t) => cb(t));
}

function pickCode(e: React.ChangeEvent<HTMLInputElement>, cb: (hex: string) => void) {
  const f = e.target.files?.[0];
  if (!f) return;
  if (f.type === "application/json" || f.type.startsWith("text/")) {
    f.text().then((t) => cb(normalizeHex((t || "").trim())));
    return;
  }
  f.arrayBuffer().then((buf) => cb("0x" + toHex(new Uint8Array(buf))));
}

function toHex(b: Uint8Array): string {
  let s = "";
  for (let i = 0; i < b.length; i++) s += b[i].toString(16).padStart(2, "0");
  return s;
}

function normalizeHex(s: string): string {
  const t = (s || "").trim();
  if (!t) return "";
  return t.startsWith("0x") || t.startsWith("0X") ? t : "0x" + t.replace(/^0x/i, "");
}

function summarizeManifest(text: string): { name?: string; version?: string; hasAbi: boolean } {
  try {
    const o = JSON.parse(text);
    return {
      name: pickString(o?.name),
      version: pickString(o?.version),
      hasAbi: !!o?.abi,
    };
  } catch {
    return { hasAbi: false };
  }
}

function extractAbi(manifestText: string): string {
  try {
    const o = JSON.parse(manifestText);
    if (!o?.abi) return "";
    return JSON.stringify(o.abi);
  } catch {
    return "";
  }
}

function summarizeAbi(text: string): { functions: number | string; events: number | string } {
  if (!text?.trim()) return { functions: "—", events: "—" };
  try {
    const o = JSON.parse(text);
    const f = Array.isArray(o?.functions) ? o.functions.length : countArrayLike(o, "functions");
    const ev = Array.isArray(o?.events) ? o.events.length : countArrayLike(o, "events");
    return { functions: f, events: ev };
  } catch {
    return { functions: "invalid", events: "invalid" };
  }
}

function countArrayLike(obj: any, key: string): number {
  try {
    if (Array.isArray(obj?.[key])) return obj[key].length;
    if (typeof obj?.[key] === "object" && obj[key]) {
      // Some schemas use an object keyed by name
      return Object.keys(obj[key]).length;
    }
  } catch {/* ignore */}
  return 0;
}

function pickString(v: unknown): string | undefined {
  return typeof v === "string" && v ? v : undefined;
}

function estimateHexSize(hex: string): number {
  const h = (hex || "").trim();
  if (!h) return 0;
  const s = h.startsWith("0x") || h.startsWith("0X") ? h.slice(2) : h;
  return Math.ceil(s.length / 2);
}

function humanBytes(n: number): string {
  if (!n || n <= 0) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v = v / 1024;
    i++;
  }
  return (i === 0 ? v.toString() : v.toFixed(2)) + " " + u[i];
}

/* --------------------------- External Consumers ------------------------- */

/**
 * Validate the DeployFormValues; useful before building a tx.
 * Returns a map of field -> error string (empty if valid).
 */
export function validateDeployInputs(values: DeployFormValues): Record<string, string> {
  const errs: Record<string, string> = {};

  // manifest
  try {
    const m = JSON.parse(values.manifestText);
    if (!m || typeof m !== "object") throw new Error("Manifest must be an object.");
  } catch (e: any) {
    errs.manifest = e?.message || "Invalid JSON";
  }

  // abi (if provided)
  if (values.abiText?.trim()) {
    try {
      const a = JSON.parse(values.abiText);
      if (!a || typeof a !== "object") throw new Error("ABI must be an object.");
    } catch (e: any) {
      errs.abi = e?.message || "Invalid JSON";
    }
  }

  // code
  if (!values.codeHex?.trim()) {
    errs.code = "Code hex is required (or upload a file).";
  } else if (!/^0x[0-9a-fA-F]*$/.test(values.codeHex.trim())) {
    errs.code = "Code must be 0x-prefixed hex.";
  }

  // value
  if (values.value?.trim()) {
    if (!isDecimal(values.value.trim()) && !isHex(values.value.trim())) {
      errs.value = "Value must be decimal or 0x-hex.";
    }
  }

  // nonce
  if (values.nonce?.trim()) {
    if (!isDecimal(values.nonce.trim())) {
      errs.nonce = "Nonce must be a decimal integer.";
    }
  }

  // fees
  if (values.feeMode === "legacy") {
    if (!isDecimal(values.gasLimit.trim())) errs.gasLimit = "Gas limit must be decimal.";
    if (!isDecimal(values.gasPrice.trim()) && !isHex(values.gasPrice.trim()))
      errs.gasPrice = "Gas price must be decimal or 0x-hex.";
  } else if (values.feeMode === "eip1559") {
    if (!isDecimal(values.gasLimit.trim())) errs.gasLimit = "Gas limit must be decimal.";
    if (!isDecimal(values.maxFeePerGas.trim()) && !isHex(values.maxFeePerGas.trim()))
      errs.maxFeePerGas = "Max fee must be decimal or 0x-hex.";
    if (
      !isDecimal(values.maxPriorityFeePerGas.trim()) &&
      !isHex(values.maxPriorityFeePerGas.trim())
    )
      errs.maxPriorityFeePerGas = "Max priority fee must be decimal or 0x-hex.";
  }

  return errs;
}

/**
 * Convert form fee fields into a normalized object.
 * Host code may merge this into a tx builder call.
 */
export function deriveFeeConfig(values: DeployFormValues): FeeConfig {
  if (values.feeMode === "legacy") {
    return {
      mode: "legacy",
      gasLimit: BigInt(values.gasLimit || "0"),
      gasPrice: parseBig(values.gasPrice || "0"),
    };
    } else if (values.feeMode === "eip1559") {
    return {
      mode: "eip1559",
      gasLimit: BigInt(values.gasLimit || "0"),
      maxFeePerGas: parseBig(values.maxFeePerGas || "0"),
      maxPriorityFeePerGas: parseBig(values.maxPriorityFeePerGas || "0"),
    };
  }
  return { mode: "auto" };
}

/* --------------------------------- utils -------------------------------- */

function isDecimal(s: string): boolean {
  return /^[0-9]+$/.test(s);
}

function isHex(s: string): boolean {
  return /^0x[0-9a-fA-F]+$/.test(s);
}

function parseBig(s: string): bigint {
  const t = s.trim();
  if (t.startsWith("0x") || t.startsWith("0X")) return BigInt(t);
  return BigInt(t || "0");
}
