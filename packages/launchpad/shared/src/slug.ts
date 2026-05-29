export function slugify(input: string): string {
  return input
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 56);
}

export function ensureUniqueSlug(base: string, exists: (candidate: string) => boolean | Promise<boolean>) {
  return (async () => {
    let candidate = base;
    let i = 1;
    while (await exists(candidate)) {
      i += 1;
      candidate = `${base}-${i}`;
      if (i > 10000) throw new Error("Could not generate unique slug");
    }
    return candidate;
  })();
}
