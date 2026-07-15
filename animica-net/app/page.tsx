import Link from "next/link";
import { ArrowRight, Mic, Radio, Users, Handshake, Rocket, BadgeCheck } from "lucide-react";
import { prisma } from "@/lib/db";
import { cachedPricesFor, cachedPrice } from "@/lib/prices";
import { Hero, type HeroCard } from "@/components/hero";
import { LiveTicker, type TickItem } from "@/components/live-ticker";
import { StatCards, SectionHeader, type Stat } from "@/components/stat-cards";
import { ProjectCard, type CardProject } from "@/components/project-card";
import { CATEGORY_LABELS, fmtCompact, fmtUsd } from "@/lib/utils";
import { AnmInternetPortal } from "@/components/anm-internet-portal";

export const revalidate = 120;

const VALUE_PROPS = [
  { icon: Mic, title: "Founder interviews", body: "Every collaboration starts with a real conversation about what you're building." },
  { icon: Radio, title: "X Spaces & AMAs", body: "We host live Spaces and Telegram AMAs to put your project in front of thousands." },
  { icon: Handshake, title: "Community raids", body: "Coordinated campaigns, templates and graphics — the whole community shows up." },
  { icon: Users, title: "Builder network", body: "Connect with developers, designers, marketers, advisors and auditors." },
];

async function toCards(projects: any[]): Promise<CardProject[]> {
  const prices = await cachedPricesFor(projects);
  return projects.map((p) => ({
    ...p,
    price: prices.get(p.id)
      ? { priceUsd: prices.get(p.id)!.priceUsd, change24h: prices.get(p.id)!.change24h ?? null }
      : null,
  }));
}

export default async function HomePage() {
  const [featuredRaw, trendingRaw, recentRaw, counts, siteStats, anm] = await Promise.all([
    prisma.project.findMany({
      where: { approved: true, featured: true },
      include: { _count: { select: { votes: true } } },
      orderBy: { updatedAt: "desc" },
      take: 6,
    }),
    prisma.project.findMany({
      where: { approved: true },
      include: { _count: { select: { votes: true } } },
      orderBy: [{ votes: { _count: "desc" } }, { activityScore: "desc" }],
      take: 8,
    }),
    prisma.project.findMany({
      where: { approved: true },
      include: { _count: { select: { votes: true } } },
      orderBy: { createdAt: "desc" },
      take: 8,
    }),
    prisma.$transaction([
      prisma.project.count({ where: { approved: true } }),
      prisma.project.count({ where: { approved: true, status: "LIVE" } }),
      prisma.project.count({ where: { approved: true, builtOnAnimica: true } }),
      prisma.project.count({ where: { approved: true, verifiedByAnimica: true } }),
    ]),
    prisma.siteStat.findMany(),
    cachedPrice({ ticker: "ANM", tokenAddress: null, tokenChain: null, coingeckoId: null }),
  ]);

  const [featured, trending, recent] = await Promise.all([toCards(featuredRaw), toCards(trendingRaw), toCards(recentRaw)]);
  const [nProjects, nLive, nOnAnimica, nVerified] = counts;
  const stat = (k: string, d = 0) => siteStats.find((s) => s.key === k)?.value ?? d;

  const stats: Stat[] = [
    { label: "$ANM price", value: anm ? fmtUsd(anm.priceUsd) : "—", accent: true },
    { label: "Projects Listed", value: fmtCompact(nProjects) },
    { label: "Live Now", value: fmtCompact(nLive) },
    { label: "Built on Animica", value: fmtCompact(nOnAnimica) },
    { label: "Verified", value: fmtCompact(nVerified) },
    { label: "Founders Interviewed", value: fmtCompact(stat("interviews")) },
    { label: "AMAs Hosted", value: fmtCompact(stat("amas")) },
  ];

  // real ticker from cached prices
  const priced = await prisma.priceCache.findMany({ orderBy: { volume24h: "desc" }, take: 16 });
  const ticker: TickItem[] = priced.map((r) => ({
    label: r.symbol ? `$${r.symbol}` : r.id,
    value: fmtUsd(r.priceUsd),
    change: r.change24h,
    accent: r.symbol === "ANM",
  }));

  const heroCards: HeroCard[] = (featured.length ? featured : trending).slice(0, 4).map((p) => ({
    name: p.name,
    cat: CATEGORY_LABELS[p.category],
    chg: p.price?.change24h != null ? `${p.price.change24h >= 0 ? "+" : ""}${p.price.change24h.toFixed(1)}%` : p.status,
  }));

  return (
    <>
      {/* animica.net now leads with the portal to the Animica Internet + browser + deploy. The Web3
          discovery platform below is preserved (its projects, quests and users are untouched). */}
      <AnmInternetPortal />

      <div className="container-x pt-14">
        <SectionHeader eyebrow="Also on animica.net" title="Discover Web3 projects & earn ANM" />
      </div>

      <Hero
        stats={{ projects: nProjects, partners: stat("partners"), community: fmtCompact(stat("reach")) + "+" }}
        cards={heroCards}
      />

      {ticker.length > 0 && <LiveTicker items={ticker} />}

      <div className="container-x py-12">
        <StatCards stats={stats} />
      </div>

      {featured.length > 0 && (
        <section className="container-x py-10">
          <SectionHeader
            eyebrow="Recently featured"
            title="Featured by Animica"
            action={
              <Link href="/projects?featured=1" className="btn-ghost !py-2 text-sm">
                View all <ArrowRight className="h-4 w-4" />
              </Link>
            }
          />
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {featured.map((p) => (
              <ProjectCard key={p.slug} p={p} />
            ))}
          </div>
        </section>
      )}

      {trending.length > 0 && (
        <section className="container-x py-10">
          <SectionHeader
            eyebrow="Most upvoted"
            title="Trending this week"
            action={
              <Link href="/trending" className="btn-ghost !py-2 text-sm">
                See trending <ArrowRight className="h-4 w-4" />
              </Link>
            }
          />
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {trending.slice(0, 8).map((p) => (
              <ProjectCard key={p.slug} p={p} />
            ))}
          </div>
        </section>
      )}

      {/* category quick nav */}
      <section className="container-x py-10">
        <SectionHeader eyebrow="Browse by category" title="Explore the ecosystem" />
        <div className="flex flex-wrap gap-2.5">
          {Object.entries(CATEGORY_LABELS).map(([k, label]) => (
            <Link
              key={k}
              href={`/projects?category=${k}`}
              className="chip px-4 py-2 text-sm hover:border-cyan/40 hover:text-cyan"
            >
              {label}
            </Link>
          ))}
        </div>
      </section>

      {/* value proposition — every featured project receives value */}
      <section className="container-x py-14">
        <div className="glass overflow-hidden p-8 sm:p-12">
          <div className="max-w-2xl">
            <p className="eyebrow mb-2">We don&apos;t sell listings — we build relationships</p>
            <h2 className="section-title">Every collaboration grows the builder <span className="grad-text">and</span> the ecosystem.</h2>
            <p className="mt-3 text-slate-400">
              We don&apos;t charge for exposure. For every project we feature, we invest real effort — and
              in return, thousands of new people discover both the project and Animica.
            </p>
          </div>
          <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {VALUE_PROPS.map((v) => (
              <div key={v.title} className="card p-5">
                <v.icon className="h-6 w-6 text-cyan" />
                <h3 className="mt-3 font-display text-base font-semibold text-white">{v.title}</h3>
                <p className="mt-1.5 text-sm text-slate-400">{v.body}</p>
              </div>
            ))}
          </div>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/submit" className="btn-iris">
              <Rocket className="h-4 w-4" /> Submit your project
            </Link>
            <Link href="/earn" className="btn-ghost">
              Earn $ANM for supporting builders
            </Link>
          </div>
        </div>
      </section>

      {recent.length > 0 && (
        <section className="container-x py-10">
          <SectionHeader eyebrow="Fresh on Animica.net" title="Recently added" />
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {recent.map((p) => (
              <ProjectCard key={p.slug} p={p} />
            ))}
          </div>
        </section>
      )}
    </>
  );
}
