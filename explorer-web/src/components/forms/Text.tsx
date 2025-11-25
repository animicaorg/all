import React, { useRef } from "react";
import Field from "./Field";
import { classNames } from "../../utils/classnames";

export type TextProps = Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "size"
> & {
  /** Field label */
  label?: React.ReactNode;
  /** Help text under the input */
  hint?: React.ReactNode;
  /** Error text; marks input invalid */
  error?: React.ReactNode;
  /** Required asterisk + aria-required */
  required?: boolean;
  /** Inline layout (label left, control right) */
  inline?: boolean;
  /** Action node to the right of the label (e.g., a button) */
  labelAction?: React.ReactNode;

  /** Left-side inline adornment (e.g., http://) */
  prefix?: React.ReactNode;
  /** Right-side inline adornment (e.g., .animica) */
  suffix?: React.ReactNode;
  /** Optional leading icon */
  leftIcon?: React.ReactNode;
  /** Optional trailing icon */
  rightIcon?: React.ReactNode;
  /** Show a clear (×) button when there is a value */
  clearable?: boolean;
  /** Extra classes for the input element */
  inputClassName?: string;
};

export default function Text({
  label,
  hint,
  error,
  required,
  inline,
  labelAction,

  prefix,
  suffix,
  leftIcon,
  rightIcon,
  clearable,

  className,
  inputClassName,
  disabled,
  id,
  type = "text",
  onChange,
  value,
  defaultValue,
  ...rest
}: TextProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  const hasValue =
    typeof value === "string"
      ? value.length > 0
      : typeof value === "number"
      ? true
      : defaultValue !== undefined
      ? String(defaultValue).length > 0
      : (inputRef.current?.value?.length ?? 0) > 0;

  const doClear = () => {
    const el = inputRef.current;
    if (!el) return;
    // For uncontrolled inputs, mutate DOM value and dispatch an input event
    const isControlled = value !== undefined;
    if (!isControlled) {
      el.value = "";
      const evt = new Event("input", { bubbles: true });
      el.dispatchEvent(evt);
    } else if (onChange) {
      // Synthesize a change event with empty value for controlled inputs
      const target = Object.create(el, {
        value: { value: "" },
      });
      const e = { ...({} as any), target, currentTarget: target };
      onChange(e);
    }
    el.focus();
  };

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
          "text-input",
          disabled && "is-disabled",
          error && "is-error",
          prefix && "has-prefix",
          suffix && "has-suffix",
          leftIcon && "has-lefticon",
          (rightIcon || (clearable && hasValue)) && "has-righticon"
        )}
      >
        {prefix ? <div className="adornment prefix">{prefix}</div> : null}
        {leftIcon ? <div className="icon left">{leftIcon}</div> : null}

        <input
          ref={inputRef}
          id={id}
          type={type}
          disabled={disabled}
          className={classNames("input", inputClassName)}
          onChange={onChange}
          value={value as any}
          defaultValue={defaultValue as any}
          {...rest}
        />

        {clearable && hasValue && !disabled ? (
          <button
            type="button"
            className="clear"
            aria-label="Clear input"
            onClick={doClear}
            tabIndex={0}
          >
            ×
          </button>
        ) : rightIcon ? (
          <div className="icon right">{rightIcon}</div>
        ) : null}

        {suffix ? <div className="adornment suffix">{suffix}</div> : null}
      </div>

      <style jsx>{`
        .text-input {
          position: relative;
          display: grid;
          grid-template-columns: auto 1fr auto;
          align-items: center;
          gap: 8px;
        }

        .text-input.has-lefticon {
          grid-template-columns: auto auto 1fr auto;
        }
        .text-input.has-righticon {
          grid-template-columns: auto 1fr auto auto;
        }
        .text-input.has-prefix.has-lefticon {
          grid-template-columns: auto auto auto 1fr auto;
        }
        .text-input.has-suffix.has-righticon {
          grid-template-columns: auto 1fr auto auto auto;
        }
        .text-input.has-prefix:not(.has-lefticon) {
          grid-template-columns: auto 1fr auto;
        }
        .text-input.has-suffix:not(.has-righticon) {
          grid-template-columns: auto 1fr auto;
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

        .icon {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          pointer-events: none;
          color: var(--text-3);
          width: 28px;
          height: 34px;
        }

        .input {
          width: 100%;
          padding: 8px 12px;
          border: 1px solid var(--border-1);
          background: var(--surface-1);
          color: var(--text-1);
          border-radius: 8px;
          outline: none;
          height: 36px;
          font-size: 0.95rem;
          transition: box-shadow 120ms ease, border-color 120ms ease;
        }

        /* Merge seamlessly with adornments */
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

        .clear {
          position: relative;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 28px;
          height: 28px;
          margin-left: -36px;
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

        @media (max-width: 840px) {
          .adornment {
            font-size: 0.85rem;
          }
        }
      `}</style>
    </Field>
  );
}
