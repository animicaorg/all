import React, { useMemo, useRef } from "react";
import Field from "./Field";
import { classNames } from "../../utils/classnames";

export type SelectOption = {
  label: React.ReactNode;
  value: string | number;
  disabled?: boolean;
};

export type SelectGroup = {
  label: React.ReactNode;
  options: SelectOption[];
  disabled?: boolean;
};

export type SelectProps = React.SelectHTMLAttributes<HTMLSelectElement> & {
  /** Field label */
  label?: React.ReactNode;
  /** Help text under the select */
  hint?: React.ReactNode;
  /** Error text; marks input invalid */
  error?: React.ReactNode;
  /** Required asterisk + aria-required */
  required?: boolean;
  /** Inline layout (label left, control right) */
  inline?: boolean;
  /** Action node to the right of the label (e.g., a button) */
  labelAction?: React.ReactNode;

  /** Provide options via data instead of children */
  options?: (SelectOption | SelectGroup)[];
  /** Placeholder option for single-select (value="") */
  placeholder?: string;

  /** Show a clear (×) button when there is a value (single-select only) */
  clearable?: boolean;

  /** Extra classes for the select element */
  selectClassName?: string;
  /** Optional left-side adornment (e.g., chain id) */
  prefix?: React.ReactNode;
  /** Optional right-side adornment (e.g., units) */
  suffix?: React.ReactNode;
};

function isGroup(x: SelectOption | SelectGroup): x is SelectGroup {
  return (x as SelectGroup).options !== undefined;
}

export default function Select({
  label,
  hint,
  error,
  required,
  inline,
  labelAction,

  options,
  placeholder,

  clearable,
  selectClassName,
  prefix,
  suffix,

  className,
  id,
  value,
  defaultValue,
  disabled,
  multiple,
  onChange,
  children,
  ...rest
}: SelectProps) {
  const selectRef = useRef<HTMLSelectElement>(null);
  const isMultiple = !!multiple;

  const hasValue = useMemo(() => {
    if (isMultiple) {
      if (Array.isArray(value)) return value.length > 0;
      if (Array.isArray(defaultValue)) return defaultValue.length > 0;
      // Try DOM value (uncontrolled)
      const el = selectRef.current;
      if (!el) return false;
      return Array.from(el.selectedOptions).length > 0;
    } else {
      if (typeof value === "string" || typeof value === "number") return String(value).length > 0;
      if (defaultValue !== undefined) return String(defaultValue).length > 0;
      const el = selectRef.current;
      return (el?.value ?? "").length > 0;
    }
  }, [value, defaultValue, isMultiple]);

  const doClear = () => {
    const el = selectRef.current;
    if (!el) return;
    if (isMultiple) return; // we only support clear on single-select here

    const isControlled = value !== undefined;
    if (!isControlled) {
      el.value = "";
      const evt = new Event("change", { bubbles: true });
      el.dispatchEvent(evt);
    } else if (onChange) {
      const target = Object.create(el, {
        value: { value: "" },
      });
      const e = { ...({} as any), target, currentTarget: target };
      onChange(e);
    }
    el.focus();
  };

  const renderedOptions = useMemo(() => {
    if (!options || options.length === 0) return children;

    const list = [];
    if (!isMultiple && placeholder) {
      list.push(
        <option key="__ph" value="" disabled={!!required}>
          {placeholder}
        </option>
      );
    }

    for (const item of options) {
      if (isGroup(item)) {
        list.push(
          <optgroup key={`g-${String(item.label)}`} label={String(item.label)} disabled={item.disabled}>
            {item.options.map((o) => (
              <option key={String(o.value)} value={o.value} disabled={o.disabled}>
                {o.label}
              </option>
            ))}
          </optgroup>
        );
      } else {
        list.push(
          <option key={String(item.value)} value={item.value} disabled={item.disabled}>
            {item.label}
          </option>
        );
      }
    }
    return list;
  }, [options, children, placeholder, required, isMultiple]);

  return (
    <Field
      label={label}
      hint={hint}
      error={error}
      required={required}
      inline={inline}
      labelAction={labelAction}
      htmlFor={id}
      className={className}
    >
      <div
        className={classNames(
          "select-input",
          disabled && "is-disabled",
          error && "is-error",
          prefix && "has-prefix",
          suffix && "has-suffix",
          isMultiple && "is-multiple",
          (clearable && hasValue && !isMultiple) && "has-clear"
        )}
      >
        {prefix ? <div className="adornment prefix">{prefix}</div> : null}

        <div className="select-shell">
          <select
            ref={selectRef}
            id={id}
            disabled={disabled}
            multiple={multiple}
            className={classNames("select", selectClassName)}
            aria-invalid={!!error}
            onChange={onChange}
            value={value as any}
            defaultValue={defaultValue as any}
            {...rest}
          >
            {renderedOptions}
          </select>

          {!isMultiple && clearable && hasValue && !disabled ? (
            <button
              type="button"
              className="clear"
              aria-label="Clear selection"
              title="Clear"
              onClick={doClear}
              tabIndex={0}
            >
              ×
            </button>
          ) : !isMultiple ? (
            <span className="chevron" aria-hidden="true">
              ▾
            </span>
          ) : null}
        </div>

        {suffix ? <div className="adornment suffix">{suffix}</div> : null}
      </div>

      <style jsx>{`
        .select-input {
          position: relative;
          display: grid;
          grid-template-columns: ${prefix ? "auto" : ""} 1fr ${suffix ? "auto" : ""};
          grid-auto-flow: column;
          align-items: center;
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

        .select-shell {
          position: relative;
          display: inline-block;
          width: 100%;
        }

        .select {
          width: 100%;
          padding: 8px 36px 8px 12px; /* leave space for chevron */
          border: 1px solid var(--border-1);
          background: var(--surface-1);
          color: var(--text-1);
          border-radius: 8px;
          outline: none;
          height: 36px;
          font-size: 0.95rem;
          transition: box-shadow 120ms ease, border-color 120ms ease;
          appearance: none;
          -webkit-appearance: none;
          -moz-appearance: none;
        }

        .is-multiple .select {
          height: auto;
          padding-right: 12px;
          min-height: 36px;
        }

        .has-prefix .select {
          border-top-left-radius: 0;
          border-bottom-left-radius: 0;
        }
        .has-suffix .select {
          border-top-right-radius: 0;
          border-bottom-right-radius: 0;
        }

        .select:focus {
          border-color: var(--brand-600);
          box-shadow: 0 0 0 2px color-mix(in srgb, var(--brand-600) 22%, transparent);
        }
        .is-error .select {
          border-color: var(--red-600);
          box-shadow: 0 0 0 2px color-mix(in srgb, var(--red-600) 20%, transparent);
        }
        .is-disabled .select {
          background: var(--surface-2);
          color: var(--text-3);
          cursor: not-allowed;
        }

        .chevron {
          position: absolute;
          right: 10px;
          top: 50%;
          transform: translateY(-50%);
          pointer-events: none;
          color: var(--text-3);
          font-size: 0.9rem;
          line-height: 1;
        }

        .clear {
          position: absolute;
          right: 6px;
          top: 50%;
          transform: translateY(-50%);
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 24px;
          height: 24px;
          border: none;
          background: transparent;
          color: var(--text-3);
          border-radius: 6px;
          cursor: pointer;
        }
        .clear:hover {
          background: var(--surface-3);
          color: var(--text-1);
        }

        /* Option & optgroup base styling (browser-dependent) */
        select option[disabled],
        select optgroup[disabled] {
          color: var(--text-3);
        }

        @media (max-width: 840px) {
          .adornment {
            font-size: 0.85rem;
          }
        }
      `}</style>
    </Field>
  );
}
