# Animica organic-SEO content plan (mining-led)

Goal: rank Animica for *winnable* mining intent now, and build the topical
authority that's the only legitimate path toward broader head terms
("cpu mining", "gpu mining", "cryptocurrency") over months — not overnight.

## Reality anchor
- Head terms (`cryptocurrency`, `mining`, `cpu mining`, `gpu mining`) are owned
  by 15-yr-old DA-90+ domains (Coinbase, Binance, NiceHash, Investopedia). A new
  domain cannot rank there from on-page SEO; that's an authority+backlinks+time
  game, or paid search.
- We win by being the *best, most specific answer* to lower-competition queries
  that real miners actually type, then climbing.

## Where the blog lives
**animica.org/blog** (Astro marketing site). Rationale: it's the hub, has the
most internal links/authority, already has `@astrojs/sitemap`, OG, JSON-LD, and
robots wired in `BaseLayout.astro`. Each post should:
- use `BaseLayout` with a unique `title`, `description`, `image`, and
  `jsonLd` of type `TechArticle` / `HowTo`.
- internally link to pool.animica.org/mine, academy tutorials, explorer.
- end with a single clear CTA: the one-command miner.

(Academy stays for gamified, reward-bearing step tutorials; the blog is for
SEO articles that funnel to academy + pool.)

## Tier 1 — brand & product (own these fast; near-zero competition)
| Target query | Article / page |
|---|---|
| animica / what is animica | hub homepage + /blog/what-is-animica |
| ANM coin / animica coin | /blog/anm-coin-explained |
| animica mining pool | pool.animica.org (done) + link from blog |
| dual mine ANM | /blog/dual-mine-anm-monero |

## Tier 2 — winnable long-tail mining intent (primary focus)
| Target query (real, beatable) | Article (HowTo) |
|---|---|
| how to CPU mine Monero (beginner) | /blog/how-to-cpu-mine-monero |
| mine two coins at once / dual mining one command | /blog/dual-mining-one-command |
| easiest Monero mining pool for beginners | /blog/easiest-monero-pool |
| best CPU for mining Monero / RandomX hashrate by CPU | /blog/best-cpu-monero-randomx |
| how to mine Monero on Windows / Linux / Mac | 3x OS-specific guides |
| is CPU mining worth it 2026 | /blog/is-cpu-mining-worth-it |
| RandomX explained | /blog/what-is-randomx |
| how to run a mining node | links to academy/run-a-node |
| mining pool payout: PPS vs PPLNS | /blog/pps-vs-pplns |

## Tier 3 — aspirational (chase later, once Tier 2 ranks + backlinks grow)
"cpu mining", "gpu mining", "best mining pool", "crypto mining for beginners".
Reached by topical clustering from Tier 2 + earned links, not on day one.

## First batch to write (highest intent → CTA)
1. how-to-cpu-mine-monero — broad funnel, ends in the one-command CTA.
2. dual-mining-one-command — the genuinely unique Animica hook.
3. easiest-monero-pool — comparison framing, low competition.
4. is-cpu-mining-worth-it — high-volume informational, honest math.
5. best-cpu-monero-randomx — long-tail with buyer/operator intent.

## Off-page (the real ranking lever — user-driven, not automatable here)
- Get listed on miningpoolstats.stream, minerstat, pool directories, "awesome
  monero" lists, RandomX pool lists → real backlinks + referral miners.
- Genuine (rule-respecting) participation in r/MoneroMining, r/gpumining.
- A GitHub README/topics for the `animica` CLI (already on PyPI) → dev links.

## Honesty rules for every post
No earnings guarantees. Show real, hardware-dependent hashrate ranges and the
actual difficulty math. Misleading profit claims rank worst with miners and
invite removal/penalties.
