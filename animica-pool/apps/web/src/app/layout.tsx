import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { Providers } from "./providers";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-mono",
});

const SITE_URL = "https://pool.animica.org";
const TITLE = "Animica Pool — mine + AI in one command";
const DESCRIPTION =
  "Run mining and AI with a single command (animica up): SHA3 proof-of-work, ENA useful-work, train-together pools, serve-while-train, an OpenAI-compatible API, and Bittensor on qualified GPUs — all paid in ANM.";

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

const NAV: [string, string][] = [
  ["/mine", "Mine"],
  ["/training-pools", "Training Pools"],
  ["/about-ena", "ENA"],
  ["/ai", "AI"],
  ["/bittensor", "Bittensor"],
  ["/workers", "Workers"],
  ["/credits", "Credits"],
  ["/stats", "Stats"],
  ["/download", "Download"],
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
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body className="min-h-screen font-sans antialiased">
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(JSON_LD) }}
        />
        <Providers>
          {/* Sticky translucent header */}
          <header className="sticky top-0 z-50 border-b border-white/10 bg-ink-900/60 backdrop-blur-xl supports-[backdrop-filter]:bg-ink-900/50">
            <nav className="mx-auto flex max-w-6xl items-center gap-5 px-4 py-3 text-sm">
              <Link
                href="/"
                className="flex items-center gap-2 text-lg font-semibold tracking-tight text-white"
              >
                <span className="grid h-7 w-7 place-items-center rounded-lg bg-grad-primary text-ink-950 shadow-glow-green">
                  <span className="text-[13px] font-black leading-none">A</span>
                </span>
                Animica <span className="grad-text">Pool</span>
              </Link>
              <div className="hidden flex-1 items-center gap-5 lg:flex">
                {NAV.map(([href, label]) => (
                  <Link
                    key={href}
                    href={href}
                    className="text-white/65 transition-colors hover:text-white"
                  >
                    {label}
                  </Link>
                ))}
              </div>
              <span className="flex-1 lg:hidden" />
              <div className="flex items-center gap-3">
                <Link href="/dashboard" className="hidden text-white/65 transition-colors hover:text-white sm:inline">
                  Dashboard
                </Link>
                <Link href="/login" className="btn-primary">
                  Sign in
                </Link>
              </div>
            </nav>
            {/* Secondary nav row for small screens so links stay reachable. */}
            <div className="border-t border-white/5 lg:hidden">
              <nav className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-1.5 px-4 py-2 text-xs">
                {NAV.map(([href, label]) => (
                  <Link
                    key={href}
                    href={href}
                    className="text-white/60 transition-colors hover:text-white"
                  >
                    {label}
                  </Link>
                ))}
              </nav>
            </div>
          </header>

          <main className="mx-auto max-w-6xl px-4 py-10 md:py-14">{children}</main>

          {/* Footer */}
          <footer className="mt-16 border-t border-white/10 bg-ink-950/40">
            <div className="mx-auto max-w-6xl px-4 py-10">
              <div className="flex flex-col gap-8 md:flex-row md:items-start md:justify-between">
                <div className="max-w-sm space-y-3">
                  <Link href="/" className="flex items-center gap-2 text-base font-semibold tracking-tight text-white">
                    <span className="grid h-6 w-6 place-items-center rounded-md bg-grad-primary text-ink-950">
                      <span className="text-[11px] font-black leading-none">A</span>
                    </span>
                    Animica <span className="grad-text">Pool</span>
                  </Link>
                  <p className="text-sm text-white/50">
                    Mine + AI in one command — train-together pools, serve-while-train, one global model. All paid in ANM.
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-x-12 gap-y-6 sm:grid-cols-3">
                  <FooterCol
                    title="Mine"
                    links={[
                      ["/mine", "Get started"],
                      ["/workers", "Workers"],
                      ["/download", "Download"],
                      ["/stats", "Stats"],
                    ]}
                  />
                  <FooterCol
                    title="AI"
                    links={[
                      ["/ai", "Inference"],
                      ["/training-pools", "Training Pools"],
                      ["/about-ena", "ENA"],
                      ["/bittensor", "Bittensor"],
                    ]}
                  />
                  <FooterCol
                    title="Account"
                    links={[
                      ["/credits", "Credits"],
                      ["/dashboard", "Dashboard"],
                      ["/api-keys", "API keys"],
                      ["/docs", "Docs"],
                    ]}
                  />
                </div>
              </div>
              <div className="mt-10 flex flex-col items-start justify-between gap-3 border-t border-white/5 pt-6 text-xs text-white/40 sm:flex-row sm:items-center">
                <p>© {new Date().getFullYear()} Animica. Useful-work economy.</p>
                <div className="flex items-center gap-4">
                  <a href="https://animica.org" className="transition-colors hover:text-white/70">animica.org</a>
                  <a href="https://explorer.animica.org" className="transition-colors hover:text-white/70">Explorer</a>
                  <a href="https://pypi.org/project/animica/" className="transition-colors hover:text-white/70">PyPI</a>
                </div>
              </div>
            </div>
          </footer>
        </Providers>
      </body>
    </html>
  );
}

function FooterCol({ title, links }: { title: string; links: [string, string][] }) {
  return (
    <div className="space-y-2.5">
      <p className="text-xs font-semibold uppercase tracking-wider text-white/40">{title}</p>
      <ul className="space-y-2">
        {links.map(([href, label]) => (
          <li key={href}>
            <Link href={href} className="text-sm text-white/60 transition-colors hover:text-white">
              {label}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
