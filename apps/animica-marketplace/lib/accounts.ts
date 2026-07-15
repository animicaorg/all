import { prisma } from './db';

export async function ensureAccount(address: string, opts: { isAgent?: boolean; displayName?: string } = {}) {
  const existing = await prisma.account.findUnique({ where: { address } });
  if (existing) return existing;
  return prisma.account.create({
    data: { address, isAgent: opts.isAgent ?? false, displayName: opts.displayName ?? null },
  });
}

export function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'listing';
}

export async function uniqueSlug(base: string): Promise<string> {
  let slug = slugify(base);
  let n = 1;
  while (await prisma.listing.findUnique({ where: { slug } })) {
    slug = `${slugify(base)}-${++n}`;
  }
  return slug;
}
