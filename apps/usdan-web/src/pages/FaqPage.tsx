export function FaqPage() {
  return (
    <section className="page">
      <h2>USDAN FAQ</h2>
      <div className="faq-list">
        <article className="card">
          <h3>When is USDAN minted?</h3>
          <p>Only after fiat settlement is confirmed and mint authorization is signed by backend controls.</p>
        </article>
        <article className="card">
          <h3>How does redemption work?</h3>
          <p>Users submit signed redemption intent, complete on-chain burn/escrow, then receive fiat payout.</p>
        </article>
        <article className="card">
          <h3>How are reserves proven?</h3>
          <p>Reserve dashboard combines ledger balances, pending queues, and signed attestation references.</p>
        </article>
      </div>
    </section>
  );
}
