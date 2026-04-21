export function shortAddr(addr: string, width = 6): string {
  if (!addr) return "";
  if (addr.length <= width * 2 + 3) return addr;
  return `${addr.slice(0, width)}...${addr.slice(-width)}`;
}

export function formatInt(value: string | number | bigint): string {
  const n = typeof value === "string" ? Number(value) : Number(value);
  if (Number.isNaN(n)) return String(value);
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 }).format(n);
}

export function nowPlusBlocks(blocks: number): number {
  return Math.floor(Date.now() / 1000) + blocks;
}

export function toTokenLabel(symbol: string, fallback: string): string {
  const s = symbol?.trim();
  return s || fallback || "TOKEN";
}
