import { permanentRedirect, redirect } from 'next/navigation';
import { prisma } from '@/lib/db';
import { STORE_TYPES } from '@/lib/storeCatalog';

export const dynamic = 'force-dynamic';

// RETIRED as an AI-listing detail page — but this route MUST stay: /marketplace/[slug] is the
// shared-slug canonical URL that shipped in third-party surfaces (the Game Lab's publish flow
// on animica.io returns `listingUrl: /marketplace/{slug}` for store DIGITAL_GOODs, and the
// Flutter wallet links here). So the store hand-off is the one behavior we keep:
//   APP / DIGITAL_GOOD  -> /marketplace/apps/[slug]  (the live App Store detail page, 308 —
//                          the slug space is unique-forever, the target is stable)
//   everything else     -> /apps (temporary — AI listings are retired in favor of Animica
//                          Python Cloud; the landing may evolve, so no 308 here)
export default async function ListingHandoff({ params }: { params: { slug: string } }) {
  const listing = await prisma.listing.findUnique({
    where: { slug: params.slug },
    select: { type: true },
  });
  if (listing && (STORE_TYPES as readonly string[]).includes(listing.type)) {
    permanentRedirect(`/marketplace/apps/${encodeURIComponent(params.slug)}`);
  }
  redirect('/apps');
}
