import { Link, useLocation } from 'react-router-dom';
import { ArrowRight, CheckCircle2 } from 'lucide-react';
import { Seo } from '../components/Seo';
import { breadcrumbJsonLd, faqJsonLd } from '../lib/seo';

type InfoPageConfig = {
  path: string;
  title: string;
  description: string;
  h1: string;
  intro: string;
  bullets: string[];
  sections: Array<{ heading: string; body: string }>;
  faqs: Array<{ question: string; answer: string }>;
  primaryCta: { label: string; to: string };
  secondaryCta: { label: string; to: string };
};

const pages: Record<string, InfoPageConfig> = {
  anm: {
    path: '/anm',
    title: 'ANM Exchange | Trade Animica on Live ANM Markets',
    description:
      'Explore Animica Exchange, a live ANM exchange where users can trade ANM against BTC, DOGE, LTC, and ZEC, claim ANM, and follow the Animica ecosystem.',
    h1: 'ANM Exchange',
    intro:
      'Animica Exchange is the trading venue for ANM markets inside the Animica ecosystem. Create an account to explore live ANM pairs, claim ANM through the airdrop flow, and use the exchange trading tools.',
    bullets: ['Live ANM trading pairs', 'Claimable ANM airdrop flow', 'Account balances, deposits, withdrawals, API keys, and automation tools'],
    sections: [
      {
        heading: 'Trade ANM against crypto majors',
        body: 'Animica Exchange supports ANM markets against BTC, DOGE, LTC, and ZEC. Each pair includes a market page with order book, recent trades, balances, and order entry.',
      },
      {
        heading: 'Built for the Animica ecosystem',
        body: 'ANM is presented here as part of the Animica product ecosystem. The exchange focuses on access, account tooling, and clear market pages rather than unsupported hype claims.',
      },
    ],
    faqs: [
      {
        question: 'Where can I trade ANM?',
        answer: 'You can trade ANM on Animica Exchange using the live BTC/ANM, DOGE/ANM, LTC/ANM, and ZEC/ANM market pages.',
      },
      {
        question: 'Can I claim ANM?',
        answer: 'Animica Exchange includes an ANM airdrop claim flow for logged-in accounts when the airdrop is enabled and the cooldown allows a claim.',
      },
    ],
    primaryCta: { label: 'Start Trading ANM', to: '/markets' },
    secondaryCta: { label: 'Claim ANM', to: '/airdrop' },
  },
  'anm-markets': {
    path: '/anm-markets',
    title: 'ANM Markets | BTC, DOGE, LTC, and ZEC Trading Pairs',
    description:
      'View live ANM markets on Animica Exchange, including BTC/ANM, DOGE/ANM, LTC/ANM, and ZEC/ANM trading pages.',
    h1: 'ANM Markets',
    intro:
      'Explore live ANM trading pairs and move from market discovery into the full trading screen with charts, order book depth, recent trades, and order entry.',
    bullets: ['BTC/ANM market page', 'DOGE/ANM market page', 'LTC/ANM market page', 'ZEC/ANM market page'],
    sections: [
      {
        heading: 'Find the ANM pair you want to trade',
        body: 'The markets page lists active Animica Exchange pairs and links directly to each trading screen.',
      },
      {
        heading: 'Use market data before placing an order',
        body: 'Pair pages show last price, order book, recent trades, and USD estimates where public pricing data is available.',
      },
    ],
    faqs: [
      {
        question: 'Which ANM pairs are available?',
        answer: 'Animica Exchange has public pages for BTC/ANM, DOGE/ANM, LTC/ANM, and ZEC/ANM.',
      },
      {
        question: 'Are ANM market pages public?',
        answer: 'Yes. Market discovery and pair pages are reachable without logging in. Placing orders requires an account.',
      },
    ],
    primaryCta: { label: 'Explore Live Markets', to: '/markets' },
    secondaryCta: { label: 'View BTC/ANM', to: '/trade/BTC-ANM' },
  },
  airdrop: {
    path: '/airdrop',
    title: 'Claim ANM | Animica Exchange Airdrop',
    description:
      'Claim ANM through the Animica Exchange airdrop flow, then explore live ANM markets and trading tools.',
    h1: 'Claim ANM',
    intro:
      'Animica Exchange includes a claimable ANM airdrop flow for accounts. The default claim cadence is 1 ANM every 4 hours while the airdrop is enabled and funded.',
    bullets: ['Claim ANM from an exchange account', 'Deposit ANM into the airdrop pool', 'Move from claim flow into live ANM markets'],
    sections: [
      {
        heading: 'How the ANM airdrop works',
        body: 'Create or sign in to an account, open the Account page, and use the claim panel. The page shows the claim amount, cooldown, pool balance, and next available claim time.',
      },
      {
        heading: 'After claiming',
        body: 'Claimed ANM appears in the account balance and can be used across available exchange flows subject to product rules and market availability.',
      },
    ],
    faqs: [
      {
        question: 'How often can I claim ANM?',
        answer: 'The default exchange setting is 1 ANM every 4 hours, but the setting is adjustable by the exchange operator.',
      },
      {
        question: 'Do I need an account to claim?',
        answer: 'Yes. The claim action is account-based so the exchange can track balances and claim cooldowns.',
      },
    ],
    primaryCta: { label: 'Create Your Account', to: '/register' },
    secondaryCta: { label: 'Explore ANM Markets', to: '/anm-markets' },
  },
  fees: {
    path: '/fees',
    title: 'Animica Exchange Fees | Trading, Deposits, and Withdrawals',
    description:
      'Review how Animica Exchange presents trading fee estimates, deposits, withdrawals, and market-specific order settings.',
    h1: 'Fees and Trading Costs',
    intro:
      'Animica Exchange shows fee estimates in the order form and exposes transfer rails for supported assets. Check the active market and account screens for the current product settings.',
    bullets: ['Order form fee estimates', 'Market-specific price ticks and size steps', 'Deposit and withdrawal rails shown per asset'],
    sections: [
      {
        heading: 'Trading fees',
        body: 'Trading fee estimates are shown before placing an order. Market settings can vary, so use the order entry panel and API market data as the source of truth.',
      },
      {
        heading: 'Deposits and withdrawals',
        body: 'The Account page lists supported transfer rails, deposit status, withdrawal fees, and withdrawal minimums where those rails are enabled.',
      },
    ],
    faqs: [
      {
        question: 'Where do I see the current fee estimate?',
        answer: 'Open a trading pair page and enter an order amount. The order form displays an estimated fee before submission.',
      },
      {
        question: 'Are withdrawal fees shown in the app?',
        answer: 'Yes. The Account page shows withdrawal rails, flat fees, and minimums for supported assets.',
      },
    ],
    primaryCta: { label: 'View Trading Pairs', to: '/markets' },
    secondaryCta: { label: 'How It Works', to: '/how-it-works' },
  },
  'how-it-works': {
    path: '/how-it-works',
    title: 'How Animica Exchange Works | Accounts, Markets, and ANM Trading',
    description:
      'Learn how to create an Animica Exchange account, claim ANM, deposit supported assets, and trade ANM markets.',
    h1: 'How Animica Exchange Works',
    intro:
      'Animica Exchange connects public market pages with account-based trading, balances, deposits, withdrawals, API keys, and built-in trading bot controls.',
    bullets: ['Create an account', 'Claim ANM when eligible', 'Explore live markets', 'Deposit supported assets and trade'],
    sections: [
      {
        heading: '1. Explore markets',
        body: 'Start with public market pages for BTC/ANM, DOGE/ANM, LTC/ANM, and ZEC/ANM.',
      },
      {
        heading: '2. Create an account',
        body: 'Accounts unlock balances, order submission, airdrop claims, deposits, withdrawals, API keys, and trading bot controls.',
      },
      {
        heading: '3. Trade or automate',
        body: 'Use the order form for manual orders or the Automation page for one active built-in bot mode per account.',
      },
    ],
    faqs: [
      {
        question: 'Can I view markets without logging in?',
        answer: 'Yes. Public market and pair pages can be viewed without an account. Trading actions require an account.',
      },
      {
        question: 'How many built-in bots can run at once?',
        answer: 'The exchange allows one running built-in trading bot per account at a time.',
      },
    ],
    primaryCta: { label: 'Create Your Account', to: '/register' },
    secondaryCta: { label: 'Explore Markets', to: '/markets' },
  },
  security: {
    path: '/security',
    title: 'Animica Exchange Security | Account and API Key Controls',
    description:
      'Understand Animica Exchange security controls including account sessions, scoped API keys, transfer rails, and authenticated trading actions.',
    h1: 'Security at Animica Exchange',
    intro:
      'Animica Exchange is built around authenticated account actions, scoped API keys, explicit transfer rails, and clear separation between public market data and private account operations.',
    bullets: ['Scoped API keys for read and trade access', 'Account-gated claims, deposits, withdrawals, and orders', 'Public market pages separated from private account actions'],
    sections: [
      {
        heading: 'API key scopes',
        body: 'API keys can be generated with read and trade scopes. Key secrets are shown once and can be revoked from the Automation page.',
      },
      {
        heading: 'Account-gated actions',
        body: 'Viewing markets is public. Submitting orders, managing balances, claiming ANM, and using transfer rails require account authentication.',
      },
    ],
    faqs: [
      {
        question: 'Can an API key manage other API keys?',
        answer: 'No. API key management is session-only in the exchange interface.',
      },
      {
        question: 'Can market pages be crawled without exposing account data?',
        answer: 'Yes. Public market pages do not require account access; private account endpoints remain authenticated.',
      },
    ],
    primaryCta: { label: 'Read API Key Docs', to: '/automation' },
    secondaryCta: { label: 'Create Your Account', to: '/register' },
  },
  about: {
    path: '/about',
    title: 'About Animica Exchange | Live ANM Trading Platform',
    description:
      'Animica Exchange is a live exchange interface for ANM trading, claim flows, deposits, withdrawals, API keys, and automation tools.',
    h1: 'About Animica Exchange',
    intro:
      'Animica Exchange is the exchange interface for the Animica ecosystem, focused on live ANM markets, account tools, and a clear path from claiming ANM to exploring trading pairs.',
    bullets: ['Live ANM markets', 'ANM airdrop claim flow', 'Account, API key, and automation tools'],
    sections: [
      {
        heading: 'Product focus',
        body: 'The exchange is designed to make ANM markets discoverable and usable with direct links to trading pairs, account balances, and exchange utilities.',
      },
      {
        heading: 'Transparent claims',
        body: 'Animica Exchange copy avoids unsupported rankings, volume claims, partnership claims, and adoption claims. Product pages focus on features visible in the app.',
      },
    ],
    faqs: [
      {
        question: 'What is Animica Exchange?',
        answer: 'Animica Exchange is a live exchange interface for ANM markets and related account tools.',
      },
      {
        question: 'What can I do after signing up?',
        answer: 'A logged-in account can place orders, view balances, claim ANM when eligible, manage transfer rails, generate API keys, and use built-in bot modes.',
      },
    ],
    primaryCta: { label: 'Explore Live Markets', to: '/markets' },
    secondaryCta: { label: 'Claim ANM', to: '/airdrop' },
  },
};

export default function InfoPage() {
  const location = useLocation();
  const slug = location.pathname.replace(/^\//, '') || 'about';
  const page = pages[slug] ?? pages.about;

  const jsonLd = [
    breadcrumbJsonLd([
      { name: 'Home', path: '/' },
      { name: page.h1, path: page.path },
    ]),
    faqJsonLd(page.faqs),
  ];

  return (
    <div className="space-y-10">
      <Seo
        title={page.title}
        description={page.description}
        path={page.path}
        structuredData={jsonLd}
      />

      <section className="grid gap-8 lg:grid-cols-[1.3fr_0.7fr] lg:items-center">
        <div>
          <p className="mb-3 text-sm font-semibold uppercase tracking-wider text-blue-300">Animica Exchange</p>
          <h1 className="text-4xl font-bold text-white md:text-5xl">{page.h1}</h1>
          <p className="mt-5 max-w-3xl text-lg text-slate-300">{page.intro}</p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Link
              to={page.primaryCta.to}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-blue-600 px-5 py-3 text-sm font-semibold text-white hover:bg-blue-500"
            >
              {page.primaryCta.label}
              <ArrowRight size={16} />
            </Link>
            <Link
              to={page.secondaryCta.to}
              className="inline-flex items-center justify-center rounded-md border border-slate-600 px-5 py-3 text-sm font-semibold text-slate-100 hover:bg-slate-800"
            >
              {page.secondaryCta.label}
            </Link>
          </div>
        </div>

        <div className="rounded-lg border border-slate-700 bg-slate-800 p-5">
          <h2 className="text-lg font-semibold text-white">Exchange Highlights</h2>
          <div className="mt-4 space-y-3">
            {page.bullets.map((bullet) => (
              <div key={bullet} className="flex gap-3 text-sm text-slate-300">
                <CheckCircle2 className="mt-0.5 shrink-0 text-green-400" size={16} />
                <span>{bullet}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="grid gap-5 md:grid-cols-2">
        {page.sections.map((section) => (
          <div key={section.heading} className="rounded-lg border border-slate-700 bg-slate-800 p-5">
            <h2 className="text-xl font-semibold text-white">{section.heading}</h2>
            <p className="mt-3 text-sm leading-6 text-slate-300">{section.body}</p>
          </div>
        ))}
      </section>

      <section className="rounded-lg bg-slate-800 p-6">
        <h2 className="text-2xl font-semibold text-white">FAQ</h2>
        <div className="mt-5 divide-y divide-slate-700">
          {page.faqs.map((faq) => (
            <div key={faq.question} className="py-4">
              <h3 className="font-semibold text-white">{faq.question}</h3>
              <p className="mt-2 text-sm leading-6 text-slate-300">{faq.answer}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-lg border border-blue-500/30 bg-blue-500/10 p-6 text-center">
        <h2 className="text-2xl font-bold text-white">Ready to use Animica Exchange?</h2>
        <p className="mx-auto mt-3 max-w-2xl text-sm text-slate-300">
          Explore live ANM markets, claim ANM when eligible, and create an account when you are ready to trade.
        </p>
        <div className="mt-5 flex flex-col justify-center gap-3 sm:flex-row">
          <Link to="/register" className="rounded-md bg-blue-600 px-5 py-3 text-sm font-semibold text-white hover:bg-blue-500">
            Create Your Account
          </Link>
          <Link to="/markets" className="rounded-md border border-slate-600 px-5 py-3 text-sm font-semibold text-white hover:bg-slate-800">
            View Trading Pairs
          </Link>
        </div>
      </section>
    </div>
  );
}
