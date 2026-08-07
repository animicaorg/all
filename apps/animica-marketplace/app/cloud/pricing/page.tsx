import { redirect, permanentRedirect } from 'next/navigation';

// /cloud/pricing used to carry its OWN plan ladder — Developer/Pro/Business at
// prices that did not match the DB `Plan` rows the checkout actually bills from.
// Two ladders for one product meant the site advertised one number and charged
// another, and a visitor had no way to tell which was real.
//
// There is now one pricing page. Python Cloud limits are tiers ON it, alongside
// the CLI and Workers entitlements, so a subscriber sees everything one plan buys
// in one place.
//
// A permanent redirect rather than a copy: the old URL is linked from the
// homepage, the docs and the schema.org offers, and a duplicate that drifts is
// how the mismatch happened in the first place.
export const dynamic = 'force-static';

export default function CloudPricingRedirect() {
  permanentRedirect('/pricing');
}
