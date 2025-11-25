import React, {
  CSSProperties,
  ReactNode,
  TextareaHTMLAttributes,
  forwardRef,
  useCallback,
  useEffect,
  useMemo,
  useRef,
} from "react";
import Field, { ControlA11yProps } from "./Field";

/**
 * TextArea — labeled, accessible multiline text input.
 *
 * Features:
 * - Works controlled (value/onChange) or uncontrolled (defaultValue)
 * - Optional autoGrow to expand with content (no scrollbars)
 * - Size variants (sm, md, lg)
 * - Optional character counter (respects maxLength)
 * - Accessible via <Field/> wrapper (label, hint, error)
 */

export type TextAreaSize = "sm" | "md" | "lg";

export type TextAreaProps = {
  label?: ReactNode;
  labelSuffix?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  disabled?: boolean;
  id?: string;
  name?: string;
  inline?: boolean;

  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;

  placeholder?: string;
  autoComplete?: TextareaHTMLAttributes<HTMLTextAreaElement>["autoComplete"];
  spellCheck?: boolean;
  readOnly?: boolean;

  rows?: number;
  maxLength?: number;

  size?: TextAreaSize;
  monospace?: boolean;
  resize?: "vertical" | "horizontal" | "none" | "both";
  autoGrow?: boolean;
  showCount?: boolean;

  className?: string;
  style?: CSSProperties;
};

const sizes: Record<TextAreaSize, { padY: number; padX: number; font: number; radius: number }> = {
  sm: { padY: 6, padX: 10, font: 13, radius: 8 },
  md: { padY: 8, padX: 12, font: 14, radius: 10 },
  lg: { padY: 12, padX: 14, font: 16, radius: 12 },
};

const baseTextAreaStyle = (
  invalid: boolean,
  size: TextAreaSize,
  monospace?: boolean,
  resize: NonNullable<TextAreaProps["resize"]>,
  minHeight?: number
): CSSProperties => {
  const s = sizes[size];
  return {
    width: "100%",
    minWidth: 0,
    fontSize: s.font,
    lineHeight: 1.4,
    padding: `${s.padY}px ${s.padX}px`,
    borderRadius: s.radius,
    border: `1px solid ${invalid ? "var(--danger-500, #ef4444)" : "var(--border-300, #e5e7eb)"}`,
    outline: "none",
    background: "var(--surface, #fff)",
    color: "var(--text, #0f172a)",
    fontFamily: monospace
      ? "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace"
      : "inherit",
    resize,
    minHeight,
    boxSizing: "border-box",
  };
};

const wrapperStyle = (invalid: boolean): CSSProperties => ({
  position: "relative",
  display: "flex",
  alignItems: "stretch",
  width: "100%",
  borderRadius: 10,
  transition: "box-shadow 120ms ease",
  ["--ring" as any]: invalid ? "rgba(239, 68, 68, .35)" : "rgba(59, 130, 246, .35)",
});

const counterStyle: CSSProperties = {
  position: "absolute",
  bottom: 6,
  right: 10,
  fontSize: 12,
  lineHeight: 1,
  color: "var(--muted-fg, #64748b)",
  pointerEvents: "none",
  background: "var(--surface, #fff)",
  padding: "2px 6px",
  borderRadius: 8,
  boxShadow: "0 0 0 1px var(--border-200, #eef2f7)",
};

function attachFocusRing(e: HTMLDivElement | null) {
  if (!e) return;
  const onFocus = () => {
    e.style.boxShadow = `0 0 0 3px var(--ring)`;
  };
  const onBlur = () => {
    e.style.boxShadow = "none";
  };
  e.addEventListener("focusin", onFocus);
  e.addEventListener("focusout", onBlur);
}

const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(props, ref) {
  const {
    label,
    labelSuffix,
    hint,
    error,
    required,
    disabled,
    id,
    name,
    inline,

    value,
    defaultValue,
    onChange,

    placeholder,
    autoComplete,
    spellCheck,
    readOnly,

    rows = 4,
    maxLength,

    size = "md",
    monospace,
    resize = "vertical",
    autoGrow = true,
    showCount = false,

    className,
    style,
  } = props;

  const invalid = Boolean(error);
  const lineH = 1.4;
  const s = sizes[size];
  const minHeight = Math.round(rows * s.font * lineH + 2 * s.padY);

  const taRef = useRef<HTMLTextAreaElement | null>(null);

  const setRef = useCallback(
    (el: HTMLTextAreaElement | null) => {
      taRef.current = el;
      if (typeof ref === "function") ref(el);
      else if (ref) (ref as any).current = el;
    },
    [ref]
  );

  const applyAutoGrow = useCallback(() => {
    const el = taRef.current;
    if (!el || !autoGrow) return;
    el.style.height = "auto";
    el.style.height = `${Math.max(minHeight, el.scrollHeight)}px`;
  }, [autoGrow, minHeight]);

  useEffect(() => {
    // Initialize height for defaultValue / controlled initial render
    applyAutoGrow();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // Recalculate when controlled value changes
    if (value !== undefined) applyAutoGrow();
  }, [value, applyAutoGrow]);

  const inputStyle = useMemo(
    () => baseTextAreaStyle(invalid, size, monospace, resize, minHeight),
    [invalid, size, monospace, resize, minHeight]
  );

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    onChange?.(e.target.value);
    if (autoGrow) applyAutoGrow();
  };

  const renderControl = (a11y: ControlA11yProps) => (
    <div ref={attachFocusRing as any} className={className} style={{ ...wrapperStyle(invalid), ...style }}>
      <textarea
        ref={setRef}
        id={a11y.id}
        name={name}
        value={value}
        defaultValue={value === undefined ? defaultValue : undefined}
        onChange={handleChange}
        placeholder={placeholder}
        autoComplete={autoComplete}
        spellCheck={spellCheck}
        readOnly={readOnly}
        disabled={disabled}
        required={required}
        rows={rows}
        maxLength={maxLength}
        aria-invalid={a11y["aria-invalid"]}
        aria-required={a11y["aria-required"]}
        aria-describedby={a11y["aria-describedby"]}
        style={inputStyle}
      />
      {showCount ? (
        <span style={counterStyle}>
          {(value ?? defaultValue ?? "").toString().length}
          {typeof maxLength === "number" ? ` / ${maxLength}` : ""}
        </span>
      ) : null}
    </div>
  );

  return (
    <Field
      label={label}
      labelSuffix={labelSuffix}
      hint={hint}
      error={error}
      required={required}
      disabled={disabled}
      id={id}
      inline={inline}
    >
      {(a11y) => renderControl(a11y)}
    </Field>
  );
});

export default TextArea;
