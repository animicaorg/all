import React, { useId } from "react";
import { classNames } from "../../utils/classnames";

export type FieldProps = {
  /** Visible label shown above (or left of) the control */
  label?: React.ReactNode;
  /** ID of the input. If omitted, we'll try to take it from the child, or generate one */
  htmlFor?: string;
  /** Optional hint/help text shown below the control */
  hint?: React.ReactNode;
  /** Error message; when present, marks control as invalid */
  error?: React.ReactNode;
  /** Adds a red asterisk after the label and sets aria-required on the control */
  required?: boolean;
  /** Inline makes the label sit on the left and control on the right */
  inline?: boolean;
  /** Extra classes on the outer wrapper */
  className?: string;
  /** Small action placed to the right of the label (e.g., a button/link) */
  labelAction?: React.ReactNode;
  /** Children: usually a single input/select/textarea element */
  children: React.ReactNode;
};

/**
 * Field
 * -----
 * Accessible form field wrapper that:
 *  - renders a label + optional hint + error
 *  - wires aria-describedby / aria-invalid onto the input
 *  - supports stacked or inline layouts
 */
export default function Field({
  label,
  htmlFor,
  hint,
  error,
  required,
  inline,
  className,
  labelAction,
  children,
}: FieldProps) {
  const reactId = useId();

  // Try to discover a child id if not provided
  const childElement = React.isValidElement(children) ? children : null;
  const childId = (childElement?.props as any)?.id as string | undefined;

  const inputId = htmlFor ?? childId ?? `fld-${reactId}`;
  const labelId = `lbl-${inputId}`;
  const hintId = hint ? `hint-${inputId}` : undefined;
  const errorId = error ? `err-${inputId}` : undefined;

  // Compose aria-describedby
  const describedBy: string | undefined = [hintId, errorId]
    .filter(Boolean)
    .join(" ") || undefined;

  const clonedChild =
    childElement &&
    React.cloneElement(childElement, {
      id: childId ?? inputId,
      "aria-describedby": mergeSpace(
        (childElement.props as any)?.["aria-describedby"],
        describedBy
      ),
      "aria-invalid": Boolean(error) || (childElement.props as any)?.["aria-invalid"] || undefined,
      "aria-required": required || (childElement.props as any)?.["aria-required"] || undefined,
      // Do not overwrite name/value/onChange etc.
    });

  return (
    <div
      className={classNames(
        "field",
        inline && "field-inline",
        error && "field-error",
        className
      )}
      role="group"
      aria-labelledby={label ? labelId : undefined}
    >
      {label ? (
        <div className={classNames("field-label", inline && "field-label-inline")}>
          <label id={labelId} htmlFor={inputId} className="label">
            <span>
              {label}
              {required ? <span className="req" aria-hidden="true"> *</span> : null}
            </span>
          </label>
          {labelAction ? <div className="label-action">{labelAction}</div> : null}
        </div>
      ) : null}

      <div className="field-control">
        {clonedChild ?? children}

        {hint ? (
          <div id={hintId} className="field-hint dim small">
            {hint}
          </div>
        ) : null}

        {error ? (
          <div id={errorId} className="field-error-text small" role="alert">
            {error}
          </div>
        ) : null}
      </div>

      {/* Local styles that rely on global CSS variables; safe across themes */}
      <style jsx>{`
        .field {
          display: grid;
          gap: 6px;
          margin: 10px 0 14px;
        }
        .field-inline {
          grid-template-columns: 180px 1fr;
          align-items: start;
          gap: 12px;
        }
        .field-label {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }
        .field-label-inline {
          margin-top: 6px;
        }
        .label {
          font-weight: 600;
          color: var(--text-1);
        }
        .label .req {
          color: var(--red-600);
        }
        .label-action {
          display: inline-flex;
          align-items: center;
          gap: 6px;
        }
        .field-control :global(input),
        .field-control :global(select),
        .field-control :global(textarea) {
          width: 100%;
        }
        .field-hint {
          margin-top: 6px;
          color: var(--text-2);
        }
        .field-error-text {
          margin-top: 6px;
          color: var(--red-600);
        }
        .field-error :global(input),
        .field-error :global(select),
        .field-error :global(textarea) {
          border-color: var(--red-600);
          box-shadow: 0 0 0 2px color-mix(in srgb, var(--red-600) 20%, transparent);
        }
        @media (max-width: 840px) {
          .field-inline {
            grid-template-columns: 1fr;
            gap: 6px;
          }
          .field-label-inline {
            margin-top: 0;
          }
        }
      `}</style>
    </div>
  );
}

function mergeSpace(a?: string, b?: string): string | undefined {
  const parts = new Set(
    [a, b]
      .filter(Boolean)
      .join(" ")
      .split(" ")
      .map((s) => s.trim())
      .filter(Boolean)
  );
  return parts.size ? Array.from(parts).join(" ") : undefined;
}
