import { Link } from 'react-router-dom';
import { SiteHeader } from '../components/SiteHeader';
import { useAuth } from '../lib/auth';

export function PricingPage() {
  const me = useAuth((s) => s.me);
  return (
    <div className="min-h-full bg-ink-950 text-ink-50">
      <SiteHeader />
      <main className="mx-auto mt-16 max-w-3xl px-4 text-center">
        <h1 className="text-3xl font-semibold">Simple pricing</h1>
        <p className="mt-2 text-ink-300">One plan to start. More tiers when usage warrants them.</p>
        <div className="mt-10 rounded-2xl border border-white/8 bg-white/[0.02] p-8 text-left">
          <div className="flex items-baseline justify-between">
            <h2 className="text-xl font-semibold">Animica Chat Pro</h2>
            <p className="text-2xl font-semibold">$10<span className="text-base text-ink-400">/mo</span></p>
          </div>
          <ul className="mt-5 space-y-2 text-sm text-ink-200">
            <li>· 1,000 AI messages every week (rolls every 7 days)</li>
            <li>· All assistant modes (General, Coding, Marketing, Animica Helper, Art Prompts, Business)</li>
            <li>· Coding agent with GitHub + GitLab tools (link in /tools)</li>
            <li>· Private chat history</li>
            <li>· Early access to new tools</li>
          </ul>
          <Link
            to={me ? '/billing' : '/login?redirectTo=/billing'}
            className="mt-7 inline-flex w-full justify-center rounded-full bg-accent-600 px-6 py-3 text-sm font-medium text-white shadow-glow hover:bg-accent-500"
          >
            {me ? 'Manage subscription' : 'Subscribe with PayPal'}
          </Link>
          <p className="mt-3 text-center text-xs text-ink-500">Cancel anytime · No ANM required</p>
        </div>
      </main>
    </div>
  );
}
