export function FaqPage() {
  return (
    <div className="grid">
      <section className="sub-hero">
        <h1 style={{ margin: 0 }}>FAQ</h1>
      </section>
      <section className="section">
        <h3>Why MetaMask connection is required?</h3>
        <p>Every order is bound to a signed EVM address. The bridge settles only to and from that signed account.</p>
        <h3>Does this prove ownership of my Animica address too?</h3>
        <p>No. Unless Animica wallet signing is enabled, the Animica side remains order-bound but not cryptographically co-owned.</p>
        <h3>Can I change destination after creating an order?</h3>
        <p>No. Destination address and amount are immutable after order creation.</p>
        <h3>What happens if amount/sender mismatch occurs?</h3>
        <p>The order is routed to manual review and never auto-settled.</p>
      </section>
    </div>
  );
}

