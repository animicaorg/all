import React, {
  CSSProperties,
  InputHTMLAttributes,
  ReactNode,
  forwardRef,
  useMemo,
  useState,
} from "react";
import Field, { ControlA11yProps } from "./Field";

/**
 * Text — labeled, accessible text input with optional prefix/suffix adornments.
 *
 * Example:
 *   <Text
 *     label="Address"
 *     placeholder="anim1..."
 *     value={addr}
 *     onChange={(v) => setAddr(v)}
 *     hint="Bech32m address"
 *     required
 *   />
 *
 * Notes:
 * - Uses <Field> for label/hint/error wiring and a11y attributes.
 * - Works as controlled (value/onChange) or uncontrolled (defaultValue).
 * - `type` supports "text" | "password" | "email" | "url" | "search" | "number".
 * - `revealToggle` shows a small toggle to reveal password text.
 */

export type TextSize = "sm" | "md" | "lg";

export type TextProps = {
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
  autoComplete?: InputHTMLAttributes<HTMLInputElement>["autoComplete"];
  spellCheck?: boolean;
  readOnly?: boolean;
  inputMode?: InputHTMLAttributes<HTMLInputElement>["inputMode"];
  maxLength?: number;
  pattern?: string;

  type?: "text" | "password" | "email" | "url" | "search" | "number";
  min?: number | string;
  max?: number | string;
  step?: number | string;

  size?: TextSize;
  monospace?: boolean;
  prefix?: ReactNode;
  suffix?: ReactNode;
  revealToggle?: boolean; // only for type=password

  className?: string;
  style?: CSSProperties;
};

const sizes: Record<TextSize, { padY: number; padX: number; font: number; radius: number; height: number }> = {
  sm: { padY: 6, padX: 10, font: 13, radius: 8, height: 34 },
  md: { padY: 8, padX: 12, font: 14, radius: 10, height: 38 },
  lg: { padY: 12, padX: 14, font: 16, radius: 12, height: 44 },
};

const baseInputStyle = (invalid: boolean, size: TextSize, monospace?: boolean): CSSProperties => {
  const s = sizes[size];
  return {
    flex: 1,
    minWidth: 0,
    height: s.height,
    fontSize: s.font,
    lineHeight: 1.3,
    padding: `${s.padY}px ${s.padX}px`,
    borderRadius: s.radius,
    border: `1px solid ${invalid ? "var(--danger-500, #ef4444)" : "var(--border-300, #e5e7eb)"}`,
    outline: "none",
    background: "var(--surface, #fff)",
    color: "var(--text, #0f172a)",
    fontFamily: monospace ? "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace" : "inherit",
  };
};

const wrapperStyle = (invalid: boolean): CSSProperties => ({
  display: "flex",
  alignItems: "center",
  gap: 8,
  borderRadius: 10,
  // focus-within ring
  boxShadow: "none",
  transition: "box-shadow 120ms ease",
  ["--ring" as any]: invalid ? "rgba(239, 68, 68, .35)" : "rgba(59, 130, 246, .35)",
});

const prefixSuffixStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  whiteSpace: "nowrap",
  fontSize: 13,
  color: "var(--muted-fg, #6b7280)",
};

const revealBtnStyle: CSSProperties = {
  border: 0,
  background: "transparent",
  padding: "0 8px",
  height: "100%",
  cursor: "pointer",
  fontSize: 12,
  color: "#64748b",
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

const Text = forwardRef<HTMLInputElement, TextProps>(function Text(props, ref) {
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
    inputMode,
    maxLength,
    pattern,
    type = "text",
    min,
    max,
    step,
    size = "md",
    monospace,
    prefix,
    suffix,
    revealToggle,
    className,
    style,
  } = props;

  const [shown, setShown] = useState(false);
  const effectiveType = type === "password" && revealToggle ? (shown ? "text" : "password") : type;

  const invalid = Boolean(error);

  const inputStyle = useMemo(() => baseInputStyle(invalid, size, monospace), [invalid, size, monospace]);

  function renderControl(a11y: ControlA11yProps) {
    return (
      <div
        className={className}
        style={{ ...wrapperStyle(invalid), ...style }}
        ref={attachFocusRing as any}
        data-invalid={invalid ? "" : undefined}
      >
        {prefix ? <span style={{ ...prefixSuffixStyle, paddingLeft: sizes[size].padX }}>{prefix}</span> : null}

        <input
          ref={ref}
          id={a11y.id}
          name={name}
          type={effectiveType}
          value={value}
          defaultValue={value === undefined ? defaultValue : undefined}
          onChange={(e) => onChange?.(e.target.value)}
          placeholder={placeholder}
          autoComplete={autoComplete}
          spellCheck={spellCheck}
          readOnly={readOnly}
          inputMode={inputMode}
          maxLength={maxLength}
          pattern={pattern}
          min={min as any}
          max={max as any}
          step={step as any}
          disabled={disabled}
          required={required}
          aria-invalid={a11y["aria-invalid"]}
          aria-required={a11y["aria-required"]}
          aria-describedby={a11y["aria-describedby"]}
          data-invalid={invalid ? "" : undefined}
          data-required={required ? "" : undefined}
          style={inputStyle}
        />

        {type === "password" && revealToggle ? (
          <button
            type="button"
            onClick={() => setShown((s) => !s)}
            aria-label={shown ? "Hide password" : "Show password"}
            style={revealBtnStyle}
            tabIndex={-1}
          >
            {shown ? "Hide" : "Show"}
          </button>
        ) : null}

        {suffix ? <span style={{ ...prefixSuffixStyle, paddingRight: sizes[size].padX }}>{suffix}</span> : null}
      </div>
    );
  }

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

export default Text;
