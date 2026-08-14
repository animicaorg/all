import type { Metadata } from 'next';
import { Code, K, Table, Callout, PageNav } from '../doc';

export const metadata: Metadata = {
  title: 'Apps — Animica Python Cloud',
  description: 'Bundle functions into a marketplace app: pricing models, purchases, authorizations and reviews.',
};

const PURCHASE = `# one-time purchase or subscription period, paid in ANM from the buyer's balance
POST /api/cloud/v1/apps/{slug}/purchase
# settles exactly-once: buyer debit, developer credit, platform fee — one transaction`;

const AUTHORIZE = `# the user's explicit, revocable grant for a sensitive-capability app
POST /api/cloud/v1/apps/{slug}/authorize
{
  "capabilities": ["SPEND_ANM"],
  "maxPerCallNanm": "1000000",
  "maxPerExecNanm": "5000000",
  "dailyCapNanm": "50000000",
  "allowedPayees": ["anim1…"],
  "expiresAt": null
}`;

export default function Page() {
  return (
    <>
      <h1>Apps</h1>
      <p className="cd-lead">
        An app bundles one or more functions into a marketplace product with its own listing page,
        pricing model, install count, reviews — and one authorization surface a user grants
        capabilities to.
      </p>

      <h2>Pricing models</h2>
      <Table
        head={['model', 'meaning']}
        rows={[
          [<K key="1">FREE</K>, 'no purchase; calls are still metered to the caller at resource cost'],
          [<K key="2">PAY_PER_USE</K>, 'the default — no purchase, callers pay per execution (metered + any per-function surcharge)'],
          [<K key="3">ONE_TIME</K>, 'buy once (priceNanm), then pay-per-use at metered cost'],
          [<K key="4">SUBSCRIPTION</K>, 'monthly ANM price for access, plus metered usage'],
        ]}
      />
      <p>
        Purchases settle with the same discipline as executions: the amount splits exactly into platform fee
        (at your snapshotted <K>feeBps</K>) and developer share, posted in one ledger transaction.
        Self-purchase is refused; a repeat purchase of an active ownership is a no-op, not a second charge.
      </p>
      <Code title="purchase">{PURCHASE}</Code>

      <h2>User authorizations (grants)</h2>
      <p>
        Sensitive capabilities — <K>SPEND_ANM</K>, <K>CALL_FUNCTION</K>, <K>CALL_APP</K>, <K>HTTP_FETCH</K> —
        require the <b>caller&apos;s</b> explicit, revocable grant before an app can use them on the caller&apos;s
        behalf. A grant carries hard budget bounds, enforced atomically at every spend:
      </p>
      <Code title="authorize an app">{AUTHORIZE}</Code>
      <ul>
        <li>
          <K>maxPerCallNanm</K> — per-payment cap · <K>maxPerExecNanm</K> — per-execution cap ·{' '}
          <K>dailyCapNanm</K> — UTC-day cap (atomic claim: concurrent executions cannot double-spend the
          last allowance)
        </li>
        <li>
          <K>allowedPayees</K> — recipient allowlist for <K>animica.wallet.pay</K>
        </li>
        <li>
          Revocation (<K>DELETE /api/cloud/v1/grants?id=…</K>) takes effect on the very next host call — the
          executor re-reads the grant at every spend.
        </li>
      </ul>

      <h2>Reviews &amp; discovery</h2>
      <ul>
        <li>
          Only genuine users may review: a review requires a purchase or at least one successful execution.
        </li>
        <li>
          The public catalog (<K>GET /api/cloud/v1/apps</K>) sorts popularity by the real execution and
          install counters, which are refreshed transactionally at settlement — never invented.
        </li>
        <li>
          Reports (<K>POST /api/cloud/v1/reports</K>) feed the moderation queue; suspended apps stop serving
          immediately (every execution checks the suspension flags).
        </li>
      </ul>

      <Callout>
        Functions can exist without an app — an app is a storefront and permission boundary, not a
        deployment requirement. Attach functions to an app by passing <K>appId</K> when creating them.
      </Callout>
      <PageNav current="/docs/cloud/apps" />
    </>
  );
}
