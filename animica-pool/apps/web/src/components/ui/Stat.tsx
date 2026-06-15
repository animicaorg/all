import type { ReactNode } from "react";

/**
 * A single glassy stat tile: big value + label, optional hint and accent.
 */
export function Stat({
  label,
  value,
  hint,
  accent = false,
  className = "",
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  accent?: boolean;
  className?: string;
}) {
  return (
    <div className={`card ${className}`}>
      <p className="text-sm text-white/50">{label}</p>
      <p
        className={`mt-1.5 text-2xl font-semibold tracking-tight ${
          accent ? "grad-text" : "text-white"
        }`}
      >
        {value}
      </p>
      {hint && <p className="mt-1 text-xs text-white/40">{hint}</p>}
    </div>
  );
}

export default Stat;
