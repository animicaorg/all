import { Link } from "react-router-dom";

export function HomePage() {
  return (
    <div className="grid">
      <section className="hero">
        <h1 style={{ margin: 0 }}>BANM Custodial Two-Way Bridge</h1>
        <p style={{ margin: 0, maxWidth: 760 }}>
          Move value between Animica and BNB Chain with immutable order binding, exact amounts, and MetaMask signature verification.
        </p>
        <div className="row">
          <Link className="btn primary" to="/bridge">
            Open Bridge
          </Link>
          <Link className="btn secondary" to="/proof-of-reserves">
            Solvency Metrics
          </Link>
        </div>
      </section>

      <section className="section">
        <h2 style={{ marginTop: 0 }}>Trust Model</h2>
        <ul className="copy-list">
          <li>Custodial operation: settlement is executed by bridge operators.</li>
          <li>MetaMask signature proves control over the connected EVM address.</li>
          <li>Without Animica-side signing, common ownership across both chains is not fully proven.</li>
          <li>Destination addresses and exact amounts are immutable after order creation.</li>
        </ul>
      </section>
    </div>
  );
}

