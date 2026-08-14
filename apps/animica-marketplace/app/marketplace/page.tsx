import { permanentRedirect } from 'next/navigation';

// RETIRED: the legacy AI marketplace home (RAG assistants / agents / generative-media
// listings). Its successor is Animica Python Cloud — deployed Python functions, apps and
// agents — whose public directory lives at /apps. 308 so crawlers and old bookmarks
// re-anchor permanently.
//
// NOTE the store pages are UNAFFECTED: /marketplace/apps and /marketplace/games are
// independent route files in the app router (app/marketplace/apps/**, app/marketplace/
// games/**) and keep serving the App Store / Game Lab surface the Flutter wallet and
// animica.io depend on. Shared-slug detail links (/marketplace/[slug]) are handled by the
// sibling [slug] route, which hands store listings off to /marketplace/apps/[slug].
export default function MarketplaceRetired() {
  permanentRedirect('/apps');
}
