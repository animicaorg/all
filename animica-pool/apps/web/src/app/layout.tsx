import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { Providers } from "./providers";

const SITE_URL = "https://pool.animica.org";
const TITLE = "Animica Pool — Dual-mine ANM + Monero (CPU mining) with one command";
const DESCRIPTION =
  "Mine Animica (ANM, SHA3) and Monero (XMR, RandomX) together on your CPU with a single command. Low-difficulty shares, pay in ANM or XMR, live hashrate & earnings stats. Also: AI inference, compute rentals, and crypto payouts.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: TITLE,
    template: "%s · Animica Pool",
  },
  description: DESCRIPTION,
  applicationName: "Animica Pool",
  keywords: [
    "mining pool",
    "CPU mining",
    "Monero mining",
    "RandomX",
    "dual mining",
    "mine Monero",
    "Animica",
    "ANM",
    "crypto mining",
    "useful work mining",
    "how to mine Monero",
  ],
  alternates: { canonical: "/" },
  robots: { index: true, follow: true },
  openGraph: {
    type: "website",
    url: SITE_URL,
    siteName: "Animica Pool",
    title: TITLE,
    description: DESCRIPTION,
    images: [{ url: "/og.png", width: 1200, height: 630, alt: "Animica Pool — dual-mine ANM + Monero with one command" }],
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESCRIPTION,
    images: ["/og.png"],
  },
};

const NAV = [
  ["/mine", "Mine"],
  ["/ai", "AI"],
  ["/bittensor", "Bittensor"],
  ["/rent", "Rent"],
  ["/workers", "Workers"],
  ["/credits", "Credits"],
  ["/stats", "Stats"],
  ["/docs", "Docs"],
];

const JSON_LD = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": `${SITE_URL}/#org`,
      name: "Animica",
      url: "https://animica.org",
      logo: `${SITE_URL}/og.png`,
      sameAs: ["https://pool.animica.org", "https://explorer.animica.org"],
    },
    {
      "@type": "WebSite",
      "@id": `${SITE_URL}/#website`,
      url: SITE_URL,
      name: "Animica Pool",
      description: DESCRIPTION,
      publisher: { "@id": `${SITE_URL}/#org` },
    },
    {
      "@type": "SoftwareApplication",
      name: "Animica CLI miner (dual-mine)",
      applicationCategory: "UtilitiesApplication",
      operatingSystem: "Windows, macOS, Linux",
      description:
        "One-command CPU miner that dual-mines Animica (ANM, SHA3) and Monero (XMR, RandomX) against the Animica Pool.",
      offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
      downloadUrl: "https://pypi.org/project/animica/",
    },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(JSON_LD) }}
        />
        <Providers>
          <header className="border-b border-white/10">
            <nav className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3 text-sm">
              <Link href="/" className="text-lg font-semibold tracking-tight">
                Animica <span className="text-neon-green">Pool</span>
              </Link>
              {NAV.map(([href, label]) => (
                <Link key={href} href={href} className="text-white/70 hover:text-white">{label}</Link>
              ))}
              <span className="flex-1" />
              <Link href="/dashboard" className="text-white/70 hover:text-white">Dashboard</Link>
              <Link href="/login" className="btn-primary">Sign in</Link>
            </nav>
          </header>
          <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
