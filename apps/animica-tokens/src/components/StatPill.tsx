import { PropsWithChildren } from "react";

export function StatPill(props: PropsWithChildren<{ label: string }>) {
  return (
    <div className="stat-pill">
      <span className="stat-label">{props.label}</span>
      <strong>{props.children}</strong>
    </div>
  );
}
