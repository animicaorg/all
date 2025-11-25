import React, {
  CSSProperties,
  ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Field, { ControlA11yProps } from "./Field";
import useMonaco from "../Editor/useMonaco";

/**
 * JsonEditor — minimal JSON editor with Monaco (falls back to <textarea/>).
 *
 * Features
 * - Controlled or uncontrolled
 * - Live JSON validation with debounced onChange(JSON)
 * - Optional format-on-blur
 * - Accessible label/help/error via <Field/>
 * - Readonly mode
 */

export type JsonEditorProps = {
  label?: ReactNode;
  labelSuffix?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  disabled?: boolean;
  id?: string;
  inline?: boolean;

  /** Controlled JSON value (object/array/primitive). If undefined, works uncontrolled via defaultValue. */
  value?: any;
  /** Default value for uncontrolled mode. */
  defaultValue?: any;

  /** Called with parsed JSON when input is valid (debounced). */
  onChange?: (value: any) => void;

  /** Validate and surface parse errors (default: true). */
  validate?: boolean;

  /** Pretty-format document on blur (default: true). */
  formatOnBlur?: boolean;

  /** Read-only view (no edits). */
  readOnly?: boolean;

  /** Placeholder shown when empty (Monaco overlay). */
  placeholder?: string;

  /** Min height (px). Default 160. */
  minHeight?: number;

  className?: string;
  style?: CSSProperties;
};

const wrapperStyle = (invalid: boolean): CSSProperties => ({
  position: "relative",
  width: "100%",
  minWidth: 0,
  borderRadius: 10,
  border: `1px solid ${invalid ? "var(--danger-500, #ef4444)" : "var(--border-300, #e5e7eb)"}`,
  background: "var(--surface, #fff)",
  ["--ring" as any]: invalid ? "rgba(239, 68, 68, .35)" : "rgba(59, 130, 246, .35)",
  transition: "box-shadow 120ms ease, border-color 120ms ease",
});

function attachFocusRing(e: HTMLDivElement | null) {
  if (!e) return;
  const onFocus = () => (e.style.boxShadow = `0 0 0 3px var(--ring)`);
  const onBlur = () => (e.style.boxShadow = "none");
  e.addEventListener("focusin", onFocus);
  e.addEventListener("focusout", onBlur);
}

function stringifyPretty(v: any): string {
  try {
    return v === undefined ? "" : JSON.stringify(v, null, 2);
  } catch {
    return "";
  }
}

const JsonEditor: React.FC<JsonEditorProps> = ({
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
  validate = true,
  formatOnBlur = true,
  readOnly = false,
  placeholder,

  minHeight = 160,
  className,
  style,
}) => {
  const monaco = useMonaco(); // lazily loaded monaco instance or null on SSR
  const containerRef = useRef<HTMLDivElement | null>(null);
  const editorRef = useRef<any>(null);
  const modelRef = useRef<any>(null);
  const blurFormatInFlight = useRef(false);
  const placeholderVisibleRef = useRef<boolean>(false);

  // Internal text state mirrors the editor contents for fallback/textarea mode
  const [text, setText] = useState<string>(() =>
    stringifyPretty(value !== undefined ? value : defaultValue)
  );

  // Track parse error (if validate=true). External error prop wins visually.
  const [parseErr, setParseErr] = useState<string | null>(null);
  const invalid = Boolean(error || parseErr);

  // Controlled sync: when parent value changes, reflect into editor/text
  useEffect(() => {
    if (value === undefined) return; // uncontrolled
    const next = stringifyPretty(value);
    setText((prev) => {
      if (prev === next) return prev;
      // Update Monaco model without triggering change-on-change loops
      if (modelRef.current && modelRef.current.getValue() !== next) {
        modelRef.current.pushEditOperations(
          [],
          [{ range: modelRef.current.getFullModelRange(), text: next }],
          () => null
        );
      }
      return next;
    });
  }, [value]);

  // Debounced parser → invokes onChange(JSON) if valid
  const debouncedValidateAndEmit = useMemo(() => {
    let t: any;
    return (src: string) => {
      if (!validate && onChange) {
        try {
          // Still try to JSON.parse to keep API consistent, but don't surface errors
          const parsed = src.trim() === "" ? null : JSON.parse(src);
          onChange(parsed);
        } catch {
          /* ignore */
        }
        return;
      }
      window.clearTimeout(t);
      t = window.setTimeout(() => {
        if (!validate) return;
        if (src.trim() === "") {
          setParseErr(null);
          onChange?.(null);
          return;
        }
        try {
          const parsed = JSON.parse(src);
          setParseErr(null);
          onChange?.(parsed);
        } catch (e: any) {
          setParseErr(e?.message ?? "Invalid JSON");
        }
      }, 180);
    };
  }, [onChange, validate]);

  // Create Monaco editor
  useEffect(() => {
    if (!monaco || !containerRef.current) return;
    const m = monaco;
    const uri = m.Uri.parse(`inmemory://json-editor/${Math.random().toString(36).slice(2)}.json`);
    const model =
      m.editor.getModel(uri) ??
      m.editor.createModel(text, "json", uri);

    modelRef.current = model;

    const editor = m.editor.create(containerRef.current, {
      model,
      readOnly: readOnly || disabled,
      language: "json",
      automaticLayout: true,
      wordWrap: "on",
      tabSize: 2,
      insertSpaces: true,
      minimap: { enabled: false },
      scrollBeyondLastLine: false,
      renderWhitespace: "boundary",
      lineNumbers: "on",
      fontSize: 13,
      bracketPairColorization: { enabled: true },
      fixedOverflowWidgets: true,
      renderValidationDecorations: "on",
    } as any);

    editorRef.current = editor;

    const sub = editor.onDidChangeModelContent(() => {
      const val: string = editor.getValue();
      setText(val);
      debouncedValidateAndEmit(val);
      updatePlaceholderOverlay(val);
    });

    // Show placeholder overlay if empty
    const updatePlaceholderOverlay = (val: string) => {
      const isEmpty = val.trim() === "";
      if (!placeholder) return;
      if (isEmpty && !placeholderVisibleRef.current) {
        containerRef.current!.setAttribute("data-empty", "true");
        placeholderVisibleRef.current = true;
      } else if (!isEmpty && placeholderVisibleRef.current) {
        containerRef.current!.removeAttribute("data-empty");
        placeholderVisibleRef.current = false;
      }
    };
    updatePlaceholderOverlay(text);

    // Format-on-blur
    const blurDisposer = editor.onDidBlurEditorText(async () => {
      if (!formatOnBlur || readOnly || disabled) return;
      if (blurFormatInFlight.current) return;
      blurFormatInFlight.current = true;
      try {
        // Try to format if JSON is valid and non-empty
        const val = editor.getValue();
        if (val.trim() === "") return;
        const parsed = JSON.parse(val);
        const pretty = JSON.stringify(parsed, null, 2);
        if (pretty !== val) {
          editor.executeEdits("format", [
            { range: model.getFullModelRange(), text: pretty },
          ]);
        }
      } catch {
        // ignore formatting if invalid
      } finally {
        blurFormatInFlight.current = false;
      }
    });

    // Validation markers surface as red squiggles; we also show border color via parseErr.
    if (validate) {
      const jsonWorkerReady = (m.languages?.json as any)?.jsonDefaults?.setDiagnosticsOptions;
      if (jsonWorkerReady) {
        (m.languages.json as any).jsonDefaults.setDiagnosticsOptions({
          validate: true,
          allowComments: false,
          enableSchemaRequest: false,
        });
      }
    }

    return () => {
      blurDisposer.dispose();
      sub.dispose();
      // Dispose editor but retain model for potential reuse
      editor.dispose();
      if (model && model.isDisposed && !model.isDisposed()) {
        model.dispose();
      }
      editorRef.current = null;
      modelRef.current = null;
    };
  }, [monaco, disabled, readOnly, formatOnBlur, placeholder, text, debouncedValidateAndEmit, validate]);

  // Fallback textarea for environments where Monaco isn't ready (e.g., SSR or tests)
  const onTextAreaChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const v = e.target.value;
      setText(v);
      debouncedValidateAndEmit(v);
    },
    [debouncedValidateAndEmit]
  );

  const renderControl = (a11y: ControlA11yProps) => (
    <div
      ref={attachFocusRing as any}
      className={className}
      style={{ ...wrapperStyle(invalid), ...style, minHeight }}
    >
      {monaco ? (
        <div
          ref={containerRef}
          id={a11y.id}
          aria-invalid={a11y["aria-invalid"]}
          aria-required={a11y["aria-required"]}
          aria-describedby={a11y["aria-describedby"]}
          role="region"
          aria-label={typeof label === "string" ? label : "JSON editor"}
          style={{
            width: "100%",
            height: "100%",
            minHeight,
            borderRadius: 10,
            overflow: "hidden",
            position: "relative",
          }}
          data-empty="false"
        />
      ) : (
        <textarea
          id={a11y.id}
          value={text}
          onChange={onTextAreaChange}
          readOnly={readOnly}
          disabled={disabled}
          aria-invalid={a11y["aria-invalid"]}
          aria-required={a11y["aria-required"]}
          aria-describedby={a11y["aria-describedby"]}
          style={{
            width: "100%",
            height: minHeight,
            padding: 12,
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
            fontSize: 13,
            lineHeight: 1.5,
            border: "none",
            outline: "none",
            background: "transparent",
            resize: "vertical",
            boxSizing: "border-box",
          }}
          placeholder={placeholder}
        />
      )}

      {/* Placeholder overlay for Monaco */}
      {monaco && placeholder && (
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            top: 10,
            left: 12,
            right: 12,
            color: "var(--muted-fg, #64748b)",
            pointerEvents: "none",
            whiteSpace: "pre-wrap",
            display: "var(--placeholder-display, block)",
          }}
        >
          {/* CSS toggled via [data-empty] attribute on container */}
          <style>{`
            [data-empty="true"] + div[aria-hidden="true"],
            [data-empty="true"] ~ div[aria-hidden="true"] {
              --placeholder-display: block;
            }
            [data-empty="false"] + div[aria-hidden="true"],
            [data-empty="false"] ~ div[aria-hidden="true"] {
              --placeholder-display: none;
            }
          `}</style>
          {placeholder}
        </div>
      )}
    </div>
  );

  return (
    <Field
      label={label}
      labelSuffix={labelSuffix}
      hint={hint}
      error={error ?? parseErr}
      required={required}
      disabled={disabled}
      id={id}
      inline={inline}
    >
      {(a11y) => renderControl(a11y)}
    </Field>
  );
};

export default JsonEditor;
