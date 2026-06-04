import Link from "next/link";

export default function Home() {
  return (
    <div className="space-y-14">
      <section className="space-y-5 py-8">
        <h1 className="text-4xl font-semibold tracking-tight md:text-6xl">
          Animica <span className="text-neon-green">Pool</span>
        </h1>
        <p className="max-w-2xl text-lg text-white/70">
          Mining, AI inference, compute rentals, and crypto payouts in one useful-work economy.
        </p>
        <div className="flex flex-wrap gap-3">
          <Link href="/mine" className="btn-primary">Start Mining</Link>
          <Link href="/credits" className="btn-ghost">Buy AI Credits</Link>
          <Link href="/workers" className="btn-ghost">Connect Worker</Link>
          <Link href="/rent" className="btn-ghost">Rent Compute</Link>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        <Feature title="Mining modes" items={["Pure ANM", "Pure XMR", "Dual 50/50 ANM + XMR", "Optional ANM payouts"]} />
        <Feature title="AI inference" items={["OpenAI-compatible API", "Multi-provider routing", "Bittensor + GPU providers", "Pay with crypto"]} />
        <Feature title="Compute rental" items={["Rent CPU / GPU", "Rent AI endpoints", "Workers earn from machines", "Transparent ledger"]} />
      </section>

      <section className="card">
        <h2 className="text-xl font-medium">Payouts</h2>
        <p className="mt-2 text-white/60">
          Optional ANM payouts · NOWPayments redistribution · transparent revenue ledger. Net realized
          profit is split across compute providers, treasury, and referrals.
        </p>
      </section>
    </div>
  );
}

function Feature({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="card">
      <h3 className="text-lg font-medium text-neon-blue">{title}</h3>
      <ul className="mt-3 space-y-1 text-sm text-white/70">
        {items.map((i) => <li key={i}>• {i}</li>)}
      </ul>
    </div>
  );
}
