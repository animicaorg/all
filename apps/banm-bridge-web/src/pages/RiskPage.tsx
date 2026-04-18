export function RiskPage() {
  return (
    <div className="grid">
      <section className="sub-hero">
        <h1 style={{ margin: 0 }}>Risk Disclosures</h1>
      </section>
      <section className="section">
        <ul className="copy-list">
          <li>Custodial risk: operator custody keys and release operations are trusted components.</li>
          <li>Smart contract risk: BANM contracts may contain undiscovered vulnerabilities.</li>
          <li>Chain risk: BNB and Animica finality assumptions can change during reorg events.</li>
          <li>Operational risk: pauses, maintenance, manual review queues, and key rotation can delay settlement.</li>
          <li>Address risk: pasted Animica destination/source addresses are final per order.</li>
          <li>Signature risk: MetaMask signature proves EVM account control only.</li>
        </ul>
      </section>
    </div>
  );
}

