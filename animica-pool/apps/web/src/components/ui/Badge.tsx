import type { ReactNode } from "react";

type Tone = "default" | "green" | "blue" | "violet";

const TONES: Record<Tone, string> = {
  default: "border-white/10 bg-white/[0.05] text-white/70",
  green: "border-neon-green/30 bg-neon-green/10 text-neon-green",
  blue: "border-neon-blue/30 bg-neon-blue/10 text-neon-blue",
  violet: "border-neon-violet/30 bg-neon-violet/10 text-neon-violet",
};

export function Badge({
  children,
  tone = "default",
  className = "",
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium backdrop-blur ${TONES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

export default Badge;
