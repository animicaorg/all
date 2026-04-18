export function TermsPage() {
  return (
    <div className="grid">
      <section className="sub-hero">
        <h1 style={{ margin: 0 }}>Bridge Terms</h1>
      </section>
      <section className="section">
        <ol className="copy-list">
          <li>Each order is immutable after creation, including amount and destination fields.</li>
          <li>Bridge fees are applied per direction and reflected before settlement.</li>
          <li>Settlement proceeds only after confirmed deposits and policy checks.</li>
          <li>Ambiguous deposits are never auto-settled and move to manual review.</li>
          <li>Emergency pause can stop forward or reverse direction independently.</li>
          <li>Use of the bridge implies acceptance of custodial operation and operator controls.</li>
        </ol>
      </section>
    </div>
  );
}

