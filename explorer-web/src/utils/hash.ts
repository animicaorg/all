export function shortHash(h: string, left = 6, right = 4): string {
  if (!h) return "";
  const s = h.startsWith("0x") ? h.slice(2) : h;
  if (s.length <= left + right) return "0x" + s;
  return "0x" + s.slice(0, left) + "…"+ s.slice(-right);
}
