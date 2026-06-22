import { useEffect, useState } from "react";

// "sm" = phone (single panel + bottom tabs), "md" = tablet, "lg" = desktop.
export type Breakpoint = "sm" | "md" | "lg";

function compute(w: number): Breakpoint {
  if (w >= 1024) return "lg";
  if (w >= 768) return "md";
  return "sm";
}

export function useBreakpoint(): Breakpoint {
  const [bp, setBp] = useState<Breakpoint>(() =>
    typeof window === "undefined" ? "lg" : compute(window.innerWidth),
  );
  useEffect(() => {
    const on = () => setBp(compute(window.innerWidth));
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return bp;
}
