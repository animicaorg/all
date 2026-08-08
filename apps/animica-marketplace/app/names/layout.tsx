import type { Metadata } from 'next';

// `app/names/page.tsx` is a 'use client' component, and a client component cannot export
// `metadata` — which is why /names was the only page on the site with no canonical tag at
// all. A route-level layout is the supported place to declare it for a client page.
export const metadata: Metadata = {
  title: 'Animica names — register and resolve .anm domains',
  description:
    'Register a .anm domain, resolve one, and search the Animica Internet index of agents, listings and domains.',
  alternates: { canonical: 'https://animica.dev/names' },
  openGraph: {
    // Next replaces the parent's whole `openGraph` object when a page declares its
    // own, so the layout's default image does NOT survive here — it has to be named.
    images: ['/og.png'],
    title: 'Animica names — .anm domains',
    description: 'Register and resolve .anm domains, and search the Animica Internet index.',
    url: 'https://animica.dev/names',
    type: 'website',
    siteName: 'Animica',
  },
};

export default function NamesLayout({ children }: { children: React.ReactNode }) {
  return children;
}
