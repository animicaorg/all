type ClassValue = string | number | boolean | null | undefined | Record<string, boolean>;
export function classnames(...args: ClassValue[]): string {
  const out: string[] = [];
  for (const a of args) {
    if (!a) continue;
    if (typeof a === "string" || typeof a === "number") out.push(String(a));
    else if (typeof a === "object") for (const [k,v] of Object.entries(a)) if (v) out.push(k);
  }
  return out.join(" ");
}
export const classNames = classnames;
export default classnames;
