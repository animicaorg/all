import { Link } from 'react-router-dom';
import { ArrowRight, CheckCircle2, Gift, KeyRound, LineChart, Wallet } from 'lucide-react';
import { Seo } from '../components/Seo';
import { faqJsonLd, organizationJsonLd, websiteJsonLd } from '../lib/seo';

const faqs = [
  {
    question: 'What is Animica Exchange?',
    answer:
      'Animica Exchange is a live exchange interface for ANM markets, account balances, deposits, withdrawals, airdrop claims, API keys, and built-in trading bot controls.',
  },
  {
    question: 'Which ANM trading pairs are available?',
    answer: 'Animica Exchange has public pages for BTC/ANM, DOGE/ANM, LTC/ANM, and ZEC/ANM markets.',
  },
  {
    question: 'Can I claim ANM?',
    answer: 'The exchange includes an account-based ANM airdrop claim flow when the airdrop is enabled and funded.',
  },
];

const pairLinks = [
  { label: 'BTC/ANM', to: '/trade/BTC-ANM', detail: 'Trade ANM against Bitcoin' },
  { label: 'DOGE/ANM', to: '/trade/DOGE-ANM', detail: 'Trade ANM against Dogecoin' },
  { label: 'LTC/ANM', to: '/trade/LTC-ANM', detail: 'Trade ANM against Litecoin' },
  { label: 'ZEC/ANM', to: '/trade/ZEC-ANM', detail: 'Trade ANM against Zcash' },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <Seo
        title="Animica Exchange | Trade ANM, Claim ANM, Explore Live Markets"
        description="Animica Exchange is a live ANM exchange for BTC/ANM, DOGE/ANM, LTC/ANM, and ZEC/ANM trading, ANM claims, deposits, API keys, and exchange tools."
        path="/"
        structuredData={[websiteJsonLd(), organizationJsonLd(), faqJsonLd(faqs)]}
      />

      <header className="border-b border-slate-800 bg-slate-950/95">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <Link to="/" className="flex items-center gap-3">
            <img src="/animica-mark.svg" alt="" className="h-9 w-9" />
            <span className="text-lg font-bold text-white">Animica Exchange</span>
          </Link>
          <nav className="hidden items-center gap-6 text-sm text-slate-300 md:flex">
            <Link to="/markets" className="hover:text-white">Markets</Link>
            <Link to="/anm" className="hover:text-white">ANM</Link>
            <Link to="/airdrop" className="hover:text-white">Airdrop</Link>
            <Link to="/fees" className="hover:text-white">Fees</Link>
            <Link to="/security" className="hover:text-white">Security</Link>
          </nav>
          <div className="flex items-center gap-3">
            <Link to="/login" className="hidden text-sm font-medium text-slate-300 hover:text-white sm:inline">
              Sign In
            </Link>
            <Link to="/register" className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-500">
              Create Account
            </Link>
          </div>
        </div>
      </header>

      <main>
        <section className="mx-auto grid max-w-7xl gap-10 px-4 py-16 sm:px-6 md:py-20 lg:grid-cols-[1.15fr_0.85fr] lg:px-8">
          <div>
            <p className="mb-4 text-sm font-semibold uppercase tracking-wider text-blue-300">Live ANM trading</p>
            <h1 className="max-w-4xl text-4xl font-bold leading-tight text-white md:text-6xl">
              Trade ANM on the Animica Exchange
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-8 text-slate-300">
              Animica Exchange is an emerging altcoin exchange ecosystem with live ANM markets,
              account-based claims, deposits, API keys, and trader tools built around the Animica product roadmap.
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <Link
                to="/markets"
                className="inline-flex items-center justify-center gap-2 rounded-md bg-blue-600 px-5 py-3 text-sm font-semibold text-white hover:bg-blue-500"
              >
                Explore Live Markets
                <ArrowRight size={16} />
              </Link>
              <Link
                to="/airdrop"
                className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-600 px-5 py-3 text-sm font-semibold text-slate-100 hover:bg-slate-900"
              >
                Claim ANM
              </Link>
            </div>
            <div className="mt-8 grid gap-3 text-sm text-slate-300 sm:grid-cols-2">
              {['BTC/ANM, DOGE/ANM, LTC/ANM, and ZEC/ANM markets', 'ANM airdrop claim flow for accounts', 'Public market pages with order book and recent trades', 'Scoped API keys and built-in bot modes'].map((item) => (
                <div key={item} className="flex gap-2">
                  <CheckCircle2 className="mt-0.5 shrink-0 text-green-400" size={16} />
                  <span>{item}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900 p-5">
            <h2 className="text-xl font-semibold text-white">Start with a live ANM pair</h2>
            <div className="mt-5 space-y-3">
              {pairLinks.map((pair) => (
                <Link
                  key={pair.label}
                  to={pair.to}
                  className="block rounded-md border border-slate-800 bg-slate-950 px-4 py-3 hover:border-blue-500/60"
                >
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <div className="font-semibold text-white">{pair.label}</div>
                      <div className="mt-1 text-sm text-slate-400">{pair.detail}</div>
                    </div>
                    <ArrowRight className="text-slate-500" size={16} />
                  </div>
                </Link>
              ))}
            </div>
          </div>
        </section>

        <section className="border-y border-slate-800 bg-slate-900/70">
          <div className="mx-auto grid max-w-7xl gap-5 px-4 py-12 sm:px-6 md:grid-cols-4 lg:px-8">
            {[
              { icon: LineChart, heading: 'Live Markets', body: 'Explore ANM pair pages with charting, order book, trades, and order entry.' },
              { icon: Gift, heading: 'Claim ANM', body: 'Use the account-based airdrop panel when the claim flow is enabled.' },
              { icon: Wallet, heading: 'Deposit Assets', body: 'View transfer rails, deposit addresses, withdrawals, and balances from Account.' },
              { icon: KeyRound, heading: 'API and Bots', body: 'Generate scoped API keys and run one built-in bot mode per account.' },
            ].map((feature) => (
              <div key={feature.heading} className="rounded-lg border border-slate-800 bg-slate-950 p-5">
                <feature.icon className="text-blue-300" size={22} />
                <h2 className="mt-4 text-lg font-semibold text-white">{feature.heading}</h2>
                <p className="mt-2 text-sm leading-6 text-slate-400">{feature.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mx-auto max-w-7xl px-4 py-14 sm:px-6 lg:px-8">
          <div className="grid gap-6 lg:grid-cols-3">
            <div className="lg:col-span-1">
              <h2 className="text-3xl font-bold text-white">How to start</h2>
              <p className="mt-3 text-sm leading-6 text-slate-400">
                Move from market discovery to an account only when you are ready to claim, deposit, or place orders.
              </p>
            </div>
            <div className="grid gap-4 lg:col-span-2">
              {[
                ['1', 'Explore ANM markets', 'Open the public markets page or a pair page like BTC/ANM.'],
                ['2', 'Create your account', 'Register to unlock balances, claims, deposits, orders, API keys, and automation.'],
                ['3', 'Claim, deposit, or trade', 'Use the Account page for ANM claims and transfers, then place orders from pair pages.'],
              ].map(([number, heading, body]) => (
                <div key={number} className="flex gap-4 rounded-lg border border-slate-800 bg-slate-900 p-5">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-blue-600 text-sm font-bold text-white">{number}</div>
                  <div>
                    <h3 className="font-semibold text-white">{heading}</h3>
                    <p className="mt-1 text-sm leading-6 text-slate-400">{body}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="mx-auto max-w-7xl px-4 pb-14 sm:px-6 lg:px-8">
          <div className="rounded-lg border border-blue-500/30 bg-blue-500/10 p-7 text-center">
            <h2 className="text-3xl font-bold text-white">Start trading ANM today</h2>
            <p className="mx-auto mt-3 max-w-2xl text-sm leading-6 text-slate-300">
              Explore live ANM markets, create an account, claim ANM when eligible, and use the exchange tools from one place.
            </p>
            <div className="mt-6 flex flex-col justify-center gap-3 sm:flex-row">
              <Link to="/register" className="rounded-md bg-blue-600 px-5 py-3 text-sm font-semibold text-white hover:bg-blue-500">
                Create Your Account
              </Link>
              <Link to="/anm-markets" className="rounded-md border border-slate-600 px-5 py-3 text-sm font-semibold text-white hover:bg-slate-900">
                View Trading Pairs
              </Link>
            </div>
          </div>
        </section>

        <section className="mx-auto max-w-4xl px-4 pb-16 sm:px-6 lg:px-8">
          <h2 className="text-2xl font-bold text-white">FAQ</h2>
          <div className="mt-4 divide-y divide-slate-800 rounded-lg border border-slate-800 bg-slate-900">
            {faqs.map((faq) => (
              <div key={faq.question} className="p-5">
                <h3 className="font-semibold text-white">{faq.question}</h3>
                <p className="mt-2 text-sm leading-6 text-slate-400">{faq.answer}</p>
              </div>
            ))}
          </div>
        </section>
      </main>

      <footer className="border-t border-slate-800 px-4 py-8 text-sm text-slate-500 sm:px-6 lg:px-8">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <p>&copy; {new Date().getFullYear()} Animica Exchange. All rights reserved.</p>
          <div className="flex flex-wrap gap-4">
            <Link to="/about" className="hover:text-slate-300">About</Link>
            <Link to="/how-it-works" className="hover:text-slate-300">How It Works</Link>
            <Link to="/security" className="hover:text-slate-300">Security</Link>
            <Link to="/legal" className="hover:text-slate-300">Legal</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
