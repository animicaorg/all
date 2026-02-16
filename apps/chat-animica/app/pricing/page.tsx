export default function PricingPage() {
  return (
    <div className="card space-y-3">
      <h1 className="text-2xl font-semibold">Pricing</h1>
      <p>$20/month billed through PayPal Subscriptions.</p>
      <form action="/api/paypal/checkout" method="post">
        <button className="rounded bg-blue-600 px-4 py-2">Subscribe with PayPal</button>
      </form>
    </div>
  );
}
