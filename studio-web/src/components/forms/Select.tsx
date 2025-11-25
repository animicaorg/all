import React, {
  CSSProperties,
  ReactNode,
  SelectHTMLAttributes,
  forwardRef,
  useCallback,
  useEffect,
  useMemo,
  useRef,
} from "react";
import Field, { ControlA11yProps } from "./Field";

/**
 * Select — accessible, styled <select> with Field wrapper.
 *
 * - Works controlled (value/onChange) or uncontrolled (defaultValue)
 * - Supports option groups
 * - Optional multiple selection
 * - Size variants (sm, md, lg)
 * - Placeholder option (for single-select)
 */

export type SelectSize = "sm" | "md" | "lg";

export type SelectOption = {
  value: string | number;
  label: ReactNode;
  disabled?: boolean;
};

export type SelectGroup = {
  label: ReactNode;
  options: SelectOption[];
  disabled?: boolean;
};

export type SelectItem = SelectOption | SelectGroup;

export type SelectProps = {
  label?: ReactNode;
  labelSuffix?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  disabled?: boolean;
  id?: string;
  name?: string;
  inline?: boolean;

  options: SelectItem[];
  placeholder?: string;

  value?: string | string[];
  defaultValue?: string | string[];
  onChange?: (value: string | string[]) => void;

  size?: SelectSize;
  multiple?: boolean;

  className?: string;
  style?: CSSProperties;
} & Omit<SelectHTMLAttributes<HTMLSelectElement>, "onChange" | "value" | "defaultValue" | "size">;

const sizes: Record<SelectSize, { padY: number; padX: number; font: number; radius: number; chevron: number }> = {
  sm: { padY: 6, padX: 10, font: 13, radius: 8, chevron: 14 },
  md: { padY: 8, padX: 12, font: 14, radius: 10, chevron: 16 },
  lg: { padY: 12, padX: 14, font: 16, radius: 12, chevron: 18 },
};

const baseSelectStyle = (
  invalid: boolean,
  size: SelectSize,
  hasChevron: boolean,
  multiple: boolean
): CSSProperties => {
  const s = sizes[size];
  return {
    width: "100%",
    minWidth: 0,
    fontSize: s.font,
    lineHeight: 1.4,
    padding: multiple
      ? `${s.padY}px ${s.padX}px`
      : `${s.padY}px ${hasChevron ? s.padX + s.chevron + 12 : s.padX}px ${s.padY}px ${s.padX}px`,
    borderRadius: s.radius,
    border: `1px solid ${invalid ? "var(--danger-500, #ef4444)" : "var(--border-300, #e5e7eb)"}`,
    outline: "none",
    background: "var(--surface, #fff)",
    color: "var(--text, #0f172a)",
    appearance: "none",
    WebkitAppearance: "none",
    MozAppearance: "none",
    boxSizing: "border-box",
  } as CSSProperties;
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

function isGroup(item: SelectItem): item is SelectGroup {
  return (item as any).options && Array.isArray((item as any).options);
}

const ChevronDown: React.FC<{ size?: number; style?: CSSProperties }> = ({ size = 16, style }) => (
  <svg
    viewBox="0 0 20 20"
    width={size}
    height={size}
    aria-hidden="true"
    focusable="false"
    style={{ display: "block", ...style }}
  >
    <path
      d="M5.22 7.97a.75.75 0 0 1 1.06 0L10 11.69l3.72-3.72a.75.75 0 1 1 1.06 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0L5.22 9.03a.75.75 0 0 1 0-1.06z"
      fill="currentColor"
    />
  </svg>
);

const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(props, ref) {
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

    options,
    placeholder,

    value,
    defaultValue,
    onChange,

    size = "md",
    multiple = false,

    className,
    style,
    ...rest
  } = props;

  const invalid = Boolean(error);
  const selRef = useRef<HTMLSelectElement | null>(null);

  const setRef = useCallback(
    (el: HTMLSelectElement | null) => {
      selRef.current = el;
      if (typeof ref === "function") ref(el);
      else if (ref) (ref as any).current = el;
    },
    [ref]
  );

  // Normalize controlled value(s) to strings (what <select> expects)
  const normalizedValue = useMemo(() => {
    if (value === undefined) return undefined;
    if (Array.isArray(value)) return value.map((v) => String(v));
    return String(value);
  }, [value]);

  // Ensure defaultValue is also string(s)
  const normalizedDefault = useMemo(() => {
    if (value !== undefined) return undefined; // controlled mode
    if (defaultValue === undefined) return undefined;
    if (Array.isArray(defaultValue)) return defaultValue.map((v) => String(v));
    return String(defaultValue);
  }, [value, defaultValue]);

  const inputStyle = useMemo(
    () => baseSelectStyle(invalid, size, !multiple, multiple),
    [invalid, size, multiple]
  );

  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    if (multiple) {
      const vals: string[] = Array.from(e.target.selectedOptions).map((o) => o.value);
      onChange?.(vals);
    } else {
      onChange?.(e.target.value);
    }
  };

  // For screen readers, ensure at least one blank option if placeholder used in single-select.
  const shouldRenderPlaceholder = !multiple && placeholder && (normalizedValue === undefined || normalizedValue === "");

  useEffect(() => {
    // If dev accidentally sets both placeholder and required without value, HTML5 will block submit.
    // That's expected and fine; we deliberately render a disabled hidden placeholder to guide selection.
  }, []);

  const renderOptions = (items: SelectItem[]) =>
    items.map((item, idx) =>
      isGroup(item) ? (
        <optgroup key={`g-${idx}`} label={String(item.label)} disabled={item.disabled}>
          {item.options.map((opt, j) => (
            <option key={`g-${idx}-o-${j}`} value={String(opt.value)} disabled={opt.disabled}>
              {opt.label as any}
            </option>
          ))}
        </optgroup>
      ) : (
        <option key={`o-${idx}`} value={String(item.value)} disabled={item.disabled}>
          {item.label as any}
        </option>
      )
    );

  const renderControl = (a11y: ControlA11yProps) => (
    <div ref={attachFocusRing as any} className={className} style={{ ...wrapperStyle(invalid), ...style }}>
      <select
        ref={setRef}
        id={a11y.id}
        name={name}
        multiple={multiple}
        disabled={disabled}
        required={required}
        aria-invalid={a11y["aria-invalid"]}
        aria-required={a11y["aria-required"]}
        aria-describedby={a11y["aria-describedby"]}
        value={normalizedValue as any}
        defaultValue={normalizedDefault as any}
        onChange={handleChange}
        style={inputStyle}
        {...rest}
      >
        {shouldRenderPlaceholder ? (
          <option value="" disabled hidden>
            {placeholder}
          </option>
        ) : null}
        {renderOptions(options)}
      </select>

      {!multiple && (
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            right: 10,
            display: "flex",
            alignItems: "center",
            color: "var(--muted-fg, #64748b)",
            pointerEvents: "none",
          }}
        >
          <ChevronDown size={sizes[size].chevron} />
        </div>
      )}
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

export default Select;
