import React, { CSSProperties, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Field, { ControlA11yProps } from "./Field";
import { hexToBytes, bytesToHex } from "../../utils/bytes";

/**
 * BytesInput — user-friendly hex/base64/file input that yields bytes.
 *
 * - Accepts hex (0x… or plain) and, optionally, base64.
 * - Optional file loader; reads as raw bytes.
 * - Shows live byte length and validation errors.
 * - Controlled or uncontrolled. Emits either hex ("0x…") or Uint8Array depending on outFormat.
 */

export type BytesInputProps = {
  // Field chrome
  label?: ReactNode;
  labelSuffix?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  disabled?: boolean;
  id?: string;
  inline?: boolean;

  // Value plumbing
  value?: string | Uint8Array;       // controlled
  defaultValue?: string | Uint8Array; // uncontrolled
  onChange?: (value: string | Uint8Array) => void;

  // Behavior
  outFormat?: "hex" | "bytes"; // default "hex"
  allowBase64?: boolean;       // default true
  allowFile?: boolean;         // default true
  autoPrefix0x?: boolean;      // default true
  placeholder?: string;

  // Validation
  minBytes?: number;
  maxBytes?: number;

  // Style
  className?: string;
  style?: CSSProperties;
};

const wrapStyle = (invalid: boolean): CSSProperties => ({
  display: "flex",
  alignItems: "stretch",
  width: "100%",
  border: `1px solid ${invalid ? "var(--danger-500, #ef4444)" : "var(--border-300, #e5e7eb)"}`,
  borderRadius: 10,
  background: "var(--surface, #fff)",
  overflow: "hidden",
  transition: "box-shadow 120ms ease, border-color 120ms ease",
});

const inputStyle: CSSProperties = {
  flex: "1 1 auto",
  minWidth: 0,
  height: 40,
  lineHeight: "40px",
  padding: "0 12px",
  fontSize: 14,
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
  border: "none",
  outline: "none",
  background: "transparent",
};

const rightBarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "0 8px",
  borderLeft: "1px solid var(--border-200, #eee)",
  background: "var(--subtle, #fafafa)",
};

function isHexLike(s: string): boolean {
  const t = s.startsWith("0x") || s.startsWith("0X") ? s.slice(2) : s;
  return t.length % 2 === 0 && /^[0-9a-fA-F]*$/.test(t);
}

function ensure0x(h: string): string {
  return h && !h.startsWith("0x") && !h.startsWith("0X") ? `0x${h}` : h;
}

function looksLikeBase64(s: string): boolean {
  // ignore whitespace
  const t = s.replace(/\s+/g, "");
  if (!t || t.startsWith("0x") || t.startsWith("0X")) return false;
  if (t.length % 4 !== 0) return false;
  return /^[A-Za-z0-9+/]+={0,2}$/.test(t);
}

function fromBase64(b64: string): Uint8Array {
  const clean = b64.replace(/\s+/g, "");
  if (typeof atob === "function") {
    const bin = atob(clean);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  // Node / tests fallback
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const buf = (typeof Buffer !== "undefined" ? Buffer.from(clean, "base64") : null) as
    | Buffer
    | null;
  if (!buf) throw new Error("Base64 decode unavailable in this environment");
  return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
}

type Parsed =
  | { ok: true; bytes: Uint8Array; fmt: "hex" | "base64" | "file" }
  | { ok: false; error: string };

function parseInput(text: string, opts: { allowBase64: boolean }): Parsed {
  const t = text.trim();
  if (t === "") return { ok: true, bytes: new Uint8Array(0), fmt: "hex" }; // empty is valid zero-bytes
  if (isHexLike(t)) {
    try {
      return { ok: true, bytes: hexToBytes(ensure0x(t)), fmt: "hex" };
    } catch (e: any) {
      return { ok: false, error: e?.message ?? "Invalid hex" };
    }
  }
  if (opts.allowBase64 && looksLikeBase64(t)) {
    try {
      return { ok: true, bytes: fromBase64(t), fmt: "base64" };
    } catch (e: any) {
      return { ok: false, error: e?.message ?? "Invalid base64" };
    }
  }
  return { ok: false, error: "Expected hex (0x…) or base64" };
}

const BytesInput: React.FC<BytesInputProps> = ({
  label,
  labelSuffix,
  hint,
  error,
  required,
  disabled,
  id,
  inline,

  value,
  defaultValue,
  onChange,

  outFormat = "hex",
  allowBase64 = true,
  allowFile = true,
  autoPrefix0x = true,
  placeholder = "0x… or base64…",

  minBytes,
  maxBytes,

  className,
  style,
}) => {
  const toText = useCallback((v?: string | Uint8Array): string => {
    if (v === undefined) return "";
    if (typeof v === "string") return v;
    return "0x" + bytesToHex(v);
  }, []);

  const [text, setText] = useState<string>(() => toText(value ?? defaultValue));
  const [parseErr, setParseErr] = useState<string | null>(null);
  const [lengthErr, setLengthErr] = useState<string | null>(null);
  const [byteLen, setByteLen] = useState<number>(0);

  // Keep text in sync for controlled value
  useEffect(() => {
    if (value === undefined) return;
    const next = toText(value);
    setText((prev) => (prev === next ? prev : next));
  }, [value, toText]);

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const emitChange = useCallback(
    (bytes: Uint8Array) => {
      if (!onChange) return;
      if (outFormat === "bytes") {
        onChange(bytes);
      } else {
        const hex = (autoPrefix0x ? "0x" : "") + bytesToHex(bytes);
        onChange(hex);
      }
    },
    [onChange, outFormat, autoPrefix0x]
  );

  // Parse and validate whenever text changes
  useEffect(() => {
    const res = parseInput(text, { allowBase64 });
    if (!res.ok) {
      setParseErr(res.error);
      setByteLen(0);
      setLengthErr(null);
      return;
    }
    setParseErr(null);
    const len = res.bytes.byteLength;
    setByteLen(len);

    // Length checks
    const tooShort = typeof minBytes === "number" && len < minBytes;
    const tooLong = typeof maxBytes === "number" && len > maxBytes;
    const lenError = tooShort
      ? `Too short: ${len} bytes (min ${minBytes})`
      : tooLong
      ? `Too long: ${len} bytes (max ${maxBytes})`
      : null;
    setLengthErr(lenError);

    if (!lenError) {
      emitChange(res.bytes);
    }
  }, [text, allowBase64, minBytes, maxBytes, emitChange]);

  const invalid = Boolean(error || parseErr || lengthErr);

  const handleTextChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setText(e.target.value);
    },
    []
  );

  const handlePickFile = useCallback(() => {
    if (!allowFile || disabled) return;
    fileInputRef.current?.click();
  }, [allowFile, disabled]);

  const onFileSelected = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        const buf = await file.arrayBuffer();
        const bytes = new Uint8Array(buf);
        const hex = "0x" + bytesToHex(bytes);
        setText(hex);
      } catch (e2: any) {
        setParseErr(e2?.message ?? "Failed to read file");
      } finally {
        // reset so selecting the same file again still triggers change
        e.target.value = "";
      }
    },
    []
  );

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* ignore */
    }
  }, [text]);

  const clear = useCallback(() => {
    setText("");
  }, []);

  const suffix = useMemo(
    () => (
      <div style={rightBarStyle}>
        <span
          title="Byte length"
          style={{
            fontSize: 12,
            padding: "2px 8px",
            borderRadius: 999,
            background: "var(--chip, #e5e7eb)",
            color: "#111827",
          }}
        >
          {byteLen} bytes
        </span>
        <button
          type="button"
          onClick={copy}
          disabled={disabled}
          style={btnStyle}
          aria-label="Copy value"
          title="Copy"
        >
          ⧉
        </button>
        <button
          type="button"
          onClick={clear}
          disabled={disabled}
          style={btnStyle}
          aria-label="Clear"
          title="Clear"
        >
          ✕
        </button>
        {allowFile && (
          <>
            <button
              type="button"
              onClick={handlePickFile}
              disabled={disabled}
              style={btnStyle}
              aria-label="Load file"
              title="Load file"
            >
              ⬆
            </button>
            <input
              ref={fileInputRef}
              type="file"
              onChange={onFileSelected}
              style={{ display: "none" }}
            />
          </>
        )}
      </div>
    ),
    [byteLen, copy, clear, allowFile, handlePickFile, onFileSelected, disabled]
  );

  const renderControl = (a11y: ControlA11yProps) => (
    <div style={{ ...wrapStyle(invalid), ...style }} className={className}>
      <input
        id={a11y.id}
        aria-invalid={a11y["aria-invalid"]}
        aria-required={a11y["aria-required"]}
        aria-describedby={a11y["aria-describedby"]}
        required={required}
        disabled={disabled}
        type="text"
        placeholder={placeholder}
        value={text}
        onChange={handleTextChange}
        style={inputStyle}
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
        autoComplete="off"
        inputMode="text"
      />
      {suffix}
    </div>
  );

  return (
    <Field
      label={label}
      labelSuffix={labelSuffix}
      hint={hint}
      error={error ?? parseErr ?? lengthErr}
      required={required}
      disabled={disabled}
      id={id}
      inline={inline}
    >
      {(a11y) => renderControl(a11y)}
    </Field>
  );
};

const btnStyle: CSSProperties = {
  height: 28,
  minWidth: 28,
  padding: "0 6px",
  borderRadius: 8,
  border: "1px solid var(--border-200, #e5e7eb)",
  background: "#fff",
  fontSize: 12,
  lineHeight: "26px",
  cursor: "pointer",
};

export default BytesInput;
