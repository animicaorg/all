import type { Metadata } from "next";
import { IBM_Plex_Sans, IBM_Plex_Mono } from "next/font/google";
import Link from "next/link";
import Script from "next/script";
import "./globals.css";
import { Providers } from "./providers";
import { isConsoleHost } from "@/lib/host";

// Animica design system type: IBM Plex Sans (display + body) / IBM Plex Mono (data).
const inter = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  variable: "--font-sans",
});
const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
  variable: "--font-mono",
});

type Col = { title: string; links: [string, string][] };
interface SiteConfig {
  url: string;
  brand: string; // accent word after "Animica"
  brandShort: string; // for title template
  title: string;
  description: string;
  nav: [string, string][];
  tagline: string;
  cols: Col[];
  cta: { primary: [string, string]; secondary: [string, string] };
}

const POOL: SiteConfig = {
  url: "https://pool.animica.org",
  brand: "Pool",
  brandShort: "Animica Pool",
  title: "Animica Pool — mine + AI in one command",
  description:
    "Run mining and AI with a single command (animica up): SHA3 proof-of-work, ENA useful-work, train-together pools, serve-while-train, an OpenAI-compatible API, and Bittensor on qualified GPUs — all paid in ANM.",
  nav: [
    ["/mine", "Mine"],
    ["/training-pools", "Training Pools"],
    ["/about-ena", "ENA"],
    ["/ai", "AI"],
    ["/usage", "Usage"],
    ["/bittensor", "Bittensor"],
    ["/workers", "Workers"],
    ["/miner", "Lookup"],
    ["/credits", "Credits"],
    ["/stats", "Stats"],
    ["/download", "Download"],
    ["/docs", "Docs"],
  ],
  tagline:
    "Mine + AI in one command — train-together pools, serve-while-train, one global model. All paid in ANM.",
  cols: [
    { title: "Mine", links: [["/mine", "Get started"], ["/workers", "Workers"], ["/download", "Download"], ["/stats", "Stats"]] },
    { title: "AI", links: [["/ai", "Inference"], ["/training-pools", "Training Pools"], ["/about-ena", "ENA"], ["/bittensor", "Bittensor"]] },
    { title: "Developers", links: [["/api-keys", "API keys"], ["/usage", "Usage"], ["/webhooks", "Webhooks"], ["/billing", "Billing & limits"], ["/docs", "Docs & SDKs"]] },
  ],
  cta: { primary: ["/login", "Sign in"], secondary: ["/dashboard", "Dashboard"] },
};

const CONSOLE: SiteConfig = {
  url: "https://console.animica.org",
  brand: "AI",
  brandShort: "Animica AI",
  title: "Animica AI — the OpenAI-compatible API for your SaaS",
  description:
    "A drop-in OpenAI-compatible AI API: real token streaming, per-key spend caps, usage analytics, idempotency, and HMAC-signed webhooks. Get an API key and ship AI features in minutes. Base URL https://api.animica.org/v1.",
  nav: [
    ["/dashboard", "Dashboard"],
    ["/api-keys", "API keys"],
    ["/usage", "Usage"],
    ["/webhooks", "Webhooks"],
    ["/billing", "Billing"],
    ["/pricing", "Pricing"],
    ["/docs", "Docs"],
  ],
  tagline:
    "The OpenAI-compatible AI API for SaaS — drop-in, with real streaming, usage analytics, spend controls and signed webhooks. Paid in credits.",
  cols: [
    { title: "Platform", links: [["/ai", "Models & playground"], ["/pricing", "Pricing"], ["/docs", "Documentation"]] },
    { title: "Account", links: [["/dashboard", "Dashboard"], ["/api-keys", "API keys"], ["/usage", "Usage"], ["/webhooks", "Webhooks"], ["/billing", "Billing & limits"]] },
    { title: "Resources", links: [["/docs", "Docs & SDKs"], ["/register", "Get an API key"], ["/login", "Sign in"]] },
  ],
  cta: { primary: ["/register", "Get API key"], secondary: ["/login", "Sign in"] },
};

function site(): SiteConfig {
  return isConsoleHost() ? CONSOLE : POOL;
}

export function generateMetadata(): Metadata {
  const c = site();
  return {
    metadataBase: new URL(c.url),
    title: { default: c.title, template: `%s · ${c.brandShort}` },
    description: c.description,
    applicationName: c.brandShort,
    alternates: { canonical: "/" },
    robots: { index: true, follow: true },
    openGraph: {
      type: "website",
      url: c.url,
      siteName: c.brandShort,
      title: c.title,
      description: c.description,
      images: [{ url: "/og.png", width: 1200, height: 630, alt: c.brandShort }],
    },
    twitter: { card: "summary_large_image", title: c.title, description: c.description, images: ["/og.png"] },
  };
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const c = site();
  const isConsole = c === CONSOLE;
  const jsonLd = {
    "@context": "https://schema.org",
    "@graph": [
      { "@type": "Organization", name: "Animica", url: "https://animica.org", logo: `${c.url}/og.png`, sameAs: ["https://animica.org", "https://pool.animica.org"] },
      { "@type": "WebSite", url: c.url, name: c.brandShort, description: c.description },
    ],
  };

  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body className="min-h-screen font-sans antialiased">
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
        <Providers>
          {!isConsole && (
            <a
              href="https://animica.org/upgrade.html"
              style={{
                display: "block",
                textDecoration: "none",
                textAlign: "center",
                background: "linear-gradient(90deg,#3a2a05,#4a1d05)",
                borderBottom: "1px solid #7a5a12",
                color: "#ffd98a",
                fontSize: "14px",
                lineHeight: 1.5,
                padding: "9px 16px",
              }}
            >
              <span aria-hidden>⚠️</span>{" "}
              <strong style={{ color: "#ffb020" }}>Network upgrade required</strong> — node
              operators &amp; miners must run{" "}
              <code
                style={{
                  background: "rgba(255,255,255,.12)",
                  padding: "2px 7px",
                  borderRadius: "5px",
                  color: "#fff",
                  whiteSpace: "nowrap",
                }}
              >
                pip install -U animica==6.0.1
              </code>{" "}
              before block <strong>40,000</strong>{" "}
              <span style={{ textDecoration: "underline", whiteSpace: "nowrap" }}>
                details →
              </span>
            </a>
          )}
          <header className="sticky top-0 z-50 border-b border-white/10 bg-ink-900/60 backdrop-blur-xl supports-[backdrop-filter]:bg-ink-900/50">
            <nav className="mx-auto flex max-w-6xl items-center gap-5 px-4 py-3 text-sm">
              <Link href="/" className="flex items-center gap-2 text-lg font-semibold tracking-tight text-white">
                <span className="grid h-7 w-7 place-items-center rounded-lg bg-grad-primary text-ink-950 shadow-glow-green">
                  <span className="text-[13px] font-black leading-none">A</span>
                </span>
                Animica <span className="grad-text">{c.brand}</span>
              </Link>
              <div className="hidden flex-1 items-center gap-5 lg:flex">
                {c.nav.map(([href, label]) => (
                  <Link key={href} href={href} className="text-white/65 transition-colors hover:text-white">
                    {label}
                  </Link>
                ))}
              </div>
              <span className="flex-1 lg:hidden" />
              <div className="flex items-center gap-3">
                {!isConsole && (
                  <span className="hidden sm:inline" dangerouslySetInnerHTML={{ __html: "<anm-price-ticker></anm-price-ticker>" }} />
                )}
                <Link href={c.cta.secondary[0]} className="hidden text-white/65 transition-colors hover:text-white sm:inline">
                  {c.cta.secondary[1]}
                </Link>
                <Link href={c.cta.primary[0]} className="btn-primary">
                  {c.cta.primary[1]}
                </Link>
              </div>
            </nav>
            <div className="border-t border-white/5 lg:hidden">
              <nav className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-1.5 px-4 py-2 text-xs">
                {c.nav.map(([href, label]) => (
                  <Link key={href} href={href} className="text-white/60 transition-colors hover:text-white">
                    {label}
                  </Link>
                ))}
              </nav>
            </div>
          </header>

          <main className="mx-auto max-w-6xl px-4 py-10 md:py-14">{children}</main>

          <footer className="mt-16 border-t border-white/10 bg-ink-950/40">
            <div className="mx-auto max-w-6xl px-4 py-10">
              <div className="flex flex-col gap-8 md:flex-row md:items-start md:justify-between">
                <div className="max-w-sm space-y-3">
                  <Link href="/" className="flex items-center gap-2 text-base font-semibold tracking-tight text-white">
                    <span className="grid h-6 w-6 place-items-center rounded-md bg-grad-primary text-ink-950">
                      <span className="text-[11px] font-black leading-none">A</span>
                    </span>
                    Animica <span className="grad-text">{c.brand}</span>
                  </Link>
                  <p className="text-sm text-white/50">{c.tagline}</p>
                </div>
                <div className="grid grid-cols-2 gap-x-12 gap-y-6 sm:grid-cols-3">
                  {c.cols.map((col) => (
                    <FooterCol key={col.title} title={col.title} links={col.links} />
                  ))}
                </div>
              </div>
              <div className="mt-10 flex flex-col items-start justify-between gap-3 border-t border-white/5 pt-6 text-xs text-white/40 sm:flex-row sm:items-center">
                <p>© {new Date().getFullYear()} Animica.{isConsole ? " AI for builders." : " Useful-work economy."}</p>
                <div className="flex items-center gap-4">
                  <a href="https://nonkyc.io/market/ANM_USDT" target="_blank" rel="noopener" className="transition-colors hover:text-white/70">Buy / Trade ANM</a>
                  <a href="https://nonkyc.io/pool/ANM_USDT" target="_blank" rel="noopener" className="transition-colors hover:text-white/70">Liquidity Pool</a>
                  <a href="https://animica.org" className="transition-colors hover:text-white/70">animica.org</a>
                  {isConsole ? (
                    <>
                      <a href="https://api.animica.org/api/docs" className="transition-colors hover:text-white/70">API reference</a>
                      <a href="https://pool.animica.org" className="transition-colors hover:text-white/70">Mining</a>
                    </>
                  ) : (
                    <>
                      <a href="https://explorer.animica.org" className="transition-colors hover:text-white/70">Explorer</a>
                      <a href="https://console.animica.org" className="transition-colors hover:text-white/70">AI API</a>
                    </>
                  )}
                </div>
              </div>
            </div>
          </footer>
          <Script src="/anm-ticker.js" strategy="afterInteractive" />
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
