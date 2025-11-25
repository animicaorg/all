export function toIso(d: Date | number | string): string {
  const date = d instanceof Date ? d : new Date(d);
  return isNaN(date.getTime()) ? "" : date.toISOString();
}
export function ago(d: Date | number | string): string {
  const date = d instanceof Date ? d : new Date(d);
  const ms = Date.now() - date.getTime();
  if (!isFinite(ms)) return "";
  const s = Math.max(0, Math.floor(ms/1000));
  const m = Math.floor(s/60), h = Math.floor(m/60), dys = Math.floor(h/24);
  if (s < 60) return `${s}s ago`;
  if (m < 60) return `${m}m ago`;
  if (h < 24) return `${h}h ago`;
  return `${dys}d ago`;
}
