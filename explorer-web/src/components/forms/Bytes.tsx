import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Field from "./Field";
import { classNames } from "../../utils/classnames";

/**
 * Helpers (kept local to avoid cross-bundle surprises)
 */
function strip0x(s: string): string {
  return s.startsWith("0x") || s.startsWith("0X") ? s.slice(2) : s;
}
function isHexString(s: string): boolean {
  return /^[0-9a-fA-F]*$/.test(s);
}
function hexToBytes(hex: string): Uint8Array {
  const clean = strip0x(hex).replace(/\s+/g, "");
  if (clean.length % 2 !== 0) throw new Error("Hex length must be even");
  if (!isHexString(clean)) throw new Error("Invalid hex characters");
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(clean.substr(i * 2, 2), 16);
  }
  return out;
}
function bytesToHex(bytes: Uint8Array, with0x = true, uppercase = false): string {
  const hex = Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  const h = uppercase ? hex.toUpperCase() : hex;
  return with0x ? "0x" + h : h;
}
function base64ToBytes(b64: string): Uint8Array {
  try {
    if (typeof atob === "function") {
      const bin = atob(b64);
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      return arr;
    }
  } catch {}
  // Node / test fallback
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Buf: any = (globalThis as any).Buffer;
  if (Buf) return new Uint8Array(Buf.from(b64, "base64"));
  throw new Error("base64 decoding unavailable in this environment");
}
function bytesToBase64(bytes: Uint8Array): string {
  try {
    if (typeof btoa === "function") {
      let bin = "";
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
      return btoa(bin);
    }
  } catch {}
  // Node / test fallback
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Buf: any = (globalThis as any).Buffer;
  if (Buf) return Buf.from(bytes).toString("base64");
  throw new Error("base64 encoding unavailable in this environment");
}

export type Encoding = "hex" | "base64";

export type BytesProps = Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "value" | "defaultValue" | "onChange"
> & {
  /** Field label */
  label?: React.ReactNode;
  /** Help text below control */
  hint?: React.ReactNode;
  /** Error text; overrides internal validation errors if provided */
  error?: React.ReactNode;
  /** Required flag (adds asterisk + aria-required) */
  required?: boolean;
  /** Inline layout (label left, control right) */
  inline?: boolean;
  /** Node rendered to the right of label (e.g., a button) */
  labelAction?: React.ReactNode;

  /** Current value (controlled). Accepts Uint8Array or string (hex/base64) */
  value?: Uint8Array | string | null;
  /** Default value (uncontrolled). Accepts Uint8Array or string (hex/base64) */
  defaultValue?: Uint8Array | string | null;
  /** Change handler returns canonical bytes + metadata */
  onChange?: (value: Uint8Array, meta: { hex: string; base64: string; length: number }) => void;

  /** Input encoding shown to the user (hex/base64) */
  encoding?: Encoding;
  /** Allow toggling encoding with a button suffix */
  encodingToggle?: boolean;
  /** Display uppercase hex */
  uppercase?: boolean;
  /** Accept 0x prefix (hex) */
  allow0x?: boolean;

  /** Show a live length counter (bytes) */
  withLength?: boolean;
  /** Enforce exact byte size (overrides min/max) */
  fixedBytes?: number;
  /** Minimum byte length */
  minBytes?: number;
  /** Maximum byte length */
  maxBytes?: number;

  /** Multiline input (textarea) for large payloads */
  multiline?: boolean;
  /** Rows for textarea */
  rows?: number;

  /** Clear button (×) */
  clearable?: boolean;
  /** Copy button */
  copyable?: boolean;
  /** Paste button */
  pasteButton?: boolean;

  /** Optional left adornment */
  prefix?: React.ReactNode;
  /** Optional right adornment */
  suffix?: React.ReactNode;

  /** Extra classes for the input/textarea element */
  inputClassName?: string;
};

function normalizeToBytes(v: Uint8Array | string | null | undefined, enc: Encoding, allow0x = true) {
  if (!v) return new Uint8Array();
  if (v instanceof Uint8Array) return v;
  const s = String(v).trim();
  if (s.length === 0) return new Uint8Array();
  if (enc === "hex") return hexToBytes(allow0x ? s : strip0x(s));
  return base64ToBytes(s);
}

export default function Bytes({
  label,
  hint,
  error,
  required,
  inline,
  labelAction,

  value,
  defaultValue,
  onChange,

  encoding = "hex",
  encodingToggle = true,
  uppercase = false,
  allow0x = true,

  withLength = true,
  fixedBytes,
  minBytes,
  maxBytes,

  multiline = false,
  rows = 4,

  clearable = true,
  copyable = true,
  pasteButton = true,

  prefix,
  suffix,

  inputClassName,
  id,
  name,
  disabled,
  readOnly,
  placeholder,
  className,
  ...rest
}: BytesProps) {
  const isControlled = value !== undefined;
  const [enc, setEnc] = useState<Encoding>(encoding);
  useEffect(() => setEnc(encoding), [encoding]);

  // text shown in the input, maintained for uncontrolled or controlled syncing
  const toText = useCallback(
    (bytes: Uint8Array) => (enc === "hex" ? bytesToHex(bytes, true, uppercase) : bytesToBase64(bytes)),
    [enc, uppercase]
  );

  const initialBytes = useMemo(
    () => normalizeToBytes(isControlled ? value : defaultValue, enc, allow0x),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [] // only once for default value
  );
  const [text, setText] = useState<string>(toText(initialBytes));
  const [internalError, setInternalError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement & HTMLInputElement>(null as any);

  // Sync controlled prop → text
  useEffect(() => {
    if (isControlled) {
      try {
        const bytes = normalizeToBytes(value, enc, allow0x);
        setText(toText(bytes));
        setInternalError(null);
      } catch (e: any) {
        setInternalError(e?.message || "Invalid input");
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, enc, uppercase, allow0x]);

  // Parse current text → bytes
  const parse = useCallback(
    (t: string): Uint8Array => {
      if (t.trim() === "") return new Uint8Array();
      try {
        return enc === "hex" ? hexToBytes(allow0x ? t : strip0x(t)) : base64ToBytes(t);
      } catch (e: any) {
        throw new Error(e?.message || "Invalid input");
      }
    },
    [enc, allow0x]
  );

  const bytes = useMemo(() => {
    try {
      const b = parse(text);
      return b;
    } catch {
      return new Uint8Array();
    }
  }, [text, parse]);

  const length = bytes.length;

  // Validation: size constraints
  const sizeError = useMemo(() => {
    if (text.trim() === "") return null;
    if (internalError) return internalError;
    if (fixedBytes !== undefined && length !== fixedBytes) {
      return `Must be exactly ${fixedBytes} byte${fixedBytes === 1 ? "" : "s"} (got ${length}).`;
    }
    if (minBytes !== undefined && length < minBytes) {
      return `Must be at least ${minBytes} byte${minBytes === 1 ? "" : "s"} (got ${length}).`;
    }
    if (maxBytes !== undefined && length > maxBytes) {
      return `Must be at most ${maxBytes} byte${maxBytes === 1 ? "" : "s"} (got ${length}).`;
    }
    return null;
  }, [text, internalError, fixedBytes, minBytes, maxBytes, length]);

  const displayError = error ?? sizeError;

  const handleChange = (t: string) => {
    setText(t);
    try {
      const b = parse(t);
      setInternalError(null);
      if (onChange) {
        onChange(b, { hex: bytesToHex(b), base64: bytesToBase64(b), length: b.length });
      }
    } catch (e: any) {
      setInternalError(e?.message || "Invalid input");
    }
  };

  const handleToggleEncoding = () => {
    const currentBytes = (() => {
      try {
        return parse(text);
      } catch {
        return new Uint8Array();
      }
    })();
    const nextEnc: Encoding = enc === "hex" ? "base64" : "hex";
    setEnc(nextEnc);
    setText(nextEnc === "hex" ? bytesToHex(currentBytes, true, uppercase) : bytesToBase64(currentBytes));
    // don't call onChange here; semantic value did not change
    inputRef.current?.focus();
  };

  const doCopy = async () => {
    const b = parse(text);
    const s = enc === "hex" ? bytesToHex(b, true, uppercase) : bytesToBase64(b);
    await navigator.clipboard.writeText(s);
  };
  const doPaste = async () => {
    const s = await navigator.clipboard.readText();
    handleChange((s || "").trim());
  };
  const doClear = () => {
    handleChange("");
    inputRef.current?.focus();
  };

  // Drag & drop file → load as raw bytes
  const onDrop: React.DragEventHandler<HTMLDivElement> = async (e) => {
    e.preventDefault();
    if (disabled || readOnly) return;
    const file = e.dataTransfer?.files?.[0];
    if (!file) return;
    const buf = new Uint8Array(await file.arrayBuffer());
    const s = enc === "hex" ? bytesToHex(buf, true, uppercase) : bytesToBase64(buf);
    handleChange(s);
  };
  const onDragOver: React.DragEventHandler<HTMLDivElement> = (e) => {
    if (disabled || readOnly) return;
    e.preventDefault();
  };

  const InputTag: any = multiline ? "textarea" : "input";

  return (
    <Field
      label={label}
      hint={
        withLength ? (
          <>
            <span>{hint}</span>
            <span style={{ marginLeft: hint ? 8 : 0, color: "var(--text-3)" }}>
              {length} byte{length === 1 ? "" : "s"}
            </span>
          </>
        ) : (
          hint
        )
      }
      error={displayError}
      required={required}
      inline={inline}
      labelAction={labelAction}
      htmlFor={id}
      className={className}
    >
      <div
        className={classNames(
          "bytes-input",
          disabled && "is-disabled",
          displayError && "is-error",
          prefix && "has-prefix",
          suffix && "has-suffix",
          multiline && "is-multiline"
        )}
        onDrop={onDrop}
        onDragOver={onDragOver}
      >
        {prefix ? <div className="adornment prefix">{prefix}</div> : null}

        <div className="input-shell">
          <InputTag
            ref={inputRef}
            id={id}
            name={name}
            disabled={disabled}
            readOnly={readOnly}
            placeholder={placeholder || (enc === "hex" ? "0x…" : "base64…")}
            rows={multiline ? rows : undefined}
            className={classNames("input", inputClassName)}
            aria-invalid={!!displayError}
            value={text}
            onChange={(e: React.ChangeEvent<HTMLInputElement & HTMLTextAreaElement>) => handleChange(e.target.value)}
            spellCheck={false}
            autoComplete="off"
            {...rest}
          />

          <div className="controls">
            {encodingToggle ? (
              <button
                type="button"
                className="btn enc"
                title={`Switch to ${enc === "hex" ? "base64" : "hex"}`}
                onClick={handleToggleEncoding}
                disabled={disabled}
              >
                {enc.toUpperCase()}
              </button>
            ) : null}

            {copyable ? (
              <button type="button" className="btn" title="Copy" onClick={doCopy} disabled={disabled}>
                Copy
              </button>
            ) : null}
            {pasteButton ? (
              <button type="button" className="btn" title="Paste" onClick={doPaste} disabled={disabled}>
                Paste
              </button>
            ) : null}
            {clearable && !readOnly ? (
              <button type="button" className="btn danger" title="Clear" onClick={doClear} disabled={disabled}>
                ×
              </button>
            ) : null}
          </div>
        </div>

        {suffix ? <div className="adornment suffix">{suffix}</div> : null}
      </div>

      <style jsx>{`
        .bytes-input {
          position: relative;
          display: grid;
          grid-template-columns: ${prefix ? "auto" : ""} 1fr ${suffix ? "auto" : ""};
          grid-auto-flow: column;
          align-items: stretch;
          gap: 8px;
        }
        .adornment {
          padding: 8px 10px;
          border: 1px solid var(--border-1);
          border-radius: 8px;
          background: var(--surface-2);
          color: var(--text-2);
          white-space: nowrap;
          font-size: 0.9rem;
          line-height: 1;
          display: flex;
          align-items: center;
        }
        .adornment.prefix {
          border-top-right-radius: 0;
          border-bottom-right-radius: 0;
          border-right: none;
        }
        .adornment.suffix {
          border-top-left-radius: 0;
          border-bottom-left-radius: 0;
          border-left: none;
        }

        .input-shell {
          position: relative;
          display: flex;
          align-items: stretch;
          width: 100%;
        }

        .input {
          width: 100%;
          padding: 8px 8px;
          border: 1px solid var(--border-1);
          background: var(--surface-1);
          color: var(--text-1);
          border-radius: 8px;
          outline: none;
          min-height: 36px;
          font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New",
            monospace;
          font-size: 0.92rem;
          transition: box-shadow 120ms ease, border-color 120ms ease;
          resize: vertical;
        }
        .bytes-input:not(.is-multiline) .input {
          height: 36px;
          resize: none;
        }

        .has-prefix .input {
          border-top-left-radius: 0;
          border-bottom-left-radius: 0;
        }
        .has-suffix .input {
          border-top-right-radius: 0;
          border-bottom-right-radius: 0;
        }

        .input:focus {
          border-color: var(--brand-600);
          box-shadow: 0 0 0 2px color-mix(in srgb, var(--brand-600) 22%, transparent);
        }
        .is-error .input {
          border-color: var(--red-600);
          box-shadow: 0 0 0 2px color-mix(in srgb, var(--red-600) 20%, transparent);
        }
        .is-disabled .input {
          background: var(--surface-2);
          color: var(--text-3);
          cursor: not-allowed;
        }

        .controls {
          position: absolute;
          right: 6px;
          top: 6px;
          display: inline-flex;
          gap: 6px;
        }
        .is-multiline .controls {
          top: 6px;
        }
        .btn {
          height: 24px;
          min-width: 40px;
          padding: 0 8px;
          border: 1px solid var(--border-1);
          background: var(--surface-2);
          color: var(--text-2);
          border-radius: 6px;
          font-size: 0.75rem;
          line-height: 1;
          cursor: pointer;
        }
        .btn:hover {
          background: var(--surface-3);
          color: var(--text-1);
        }
        .btn.danger {
          border-color: var(--red-300);
        }
        .btn.danger:hover {
          background: color-mix(in srgb, var(--red-500) 16%, var(--surface-3));
          color: var(--red-800);
        }
        .btn.enc {
          font-weight: 600;
        }

        @media (max-width: 840px) {
          .adornment {
            font-size: 0.85rem;
          }
          .btn {
            min-width: 36px;
          }
        }
      `}</style>
    </Field>
  );
}
