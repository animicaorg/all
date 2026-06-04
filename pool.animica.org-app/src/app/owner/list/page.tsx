"use client";

import { useState } from "react";

export default function ListRigPage() {
  const [address, setAddress] = useState("");
  const [claim, setClaim] = useState<{ claim_worker: string; instructions: string } | null>(null);
  const [workerName, setWorkerName] = useState("");
  const [price, setPrice] = useState("0.50");
  const [coins, setCoins] = useState<"ANM" | "XMR" | "BOTH">("ANM");
  const [payoutCurrency, setPayoutCurrency] = useState("usdttrc20");
  const [payoutAddress, setPayoutAddress] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const field = "w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2";

  async function getChallenge() {
    setMsg(null);
    setBusy(true);
    try {
      const res = await fetch("/app/api/owner/claim", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ address }),
      });
      const data = await res.json();
      if (!res.ok) return setMsg(data.error || "Could not start claim");
      setClaim(data);
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    setMsg(null);
    setBusy(true);
    try {
      const res = await fetch("/app/api/owner/listings", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          address,
          workerName,
          claimWorker: claim?.claim_worker,
          pricePerHourUsd: price,
          payoutCurrency,
          payoutAddress,
          supportedCoins: coins,
        }),
      });
      const data = await res.json();
      if (!res.ok) return setMsg(data.error || "Could not list rig");
      window.location.href = "/app/owner";
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <h1 className="text-2xl font-semibold">List a rig</h1>

      <section className="space-y-3 rounded-xl border border-white/10 bg-white/5 p-5">
        <h2 className="font-medium">1. Prove your payout address</h2>
        <input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="anim1… (your mining payout address)" className={field} />
        <button onClick={getChallenge} disabled={busy || !address} className="rounded-lg bg-white/10 px-3 py-2 text-sm hover:bg-white/20 disabled:opacity-50">
          Get claim token
        </button>
        {claim && (
          <p className="rounded-lg bg-black/30 p-3 text-sm text-white/70">
            Mine for ~2 minutes with worker name{" "}
            <code className="text-blue-300">{claim.claim_worker}</code> from this address, then publish below.
          </p>
        )}
      </section>

      {claim && (
        <section className="space-y-3 rounded-xl border border-white/10 bg-white/5 p-5">
          <h2 className="font-medium">2. Listing details</h2>
          <input value={workerName} onChange={(e) => setWorkerName(e.target.value)} placeholder="rig worker name (e.g. rig-01)" className={field} />
          <label className="block text-sm">
            Price per hour (USD)
            <input value={price} onChange={(e) => setPrice(e.target.value)} className={field} />
          </label>
          <label className="block text-sm">
            Coins this rig can mine
            <select value={coins} onChange={(e) => setCoins(e.target.value as "ANM" | "XMR" | "BOTH")} className={field}>
              <option value="ANM">ANM</option>
              <option value="XMR">Monero (XMR)</option>
              <option value="BOTH">ANM + Monero</option>
            </select>
          </label>
          <label className="block text-sm">
            Payout currency
            <input value={payoutCurrency} onChange={(e) => setPayoutCurrency(e.target.value)} placeholder="usdttrc20 / btc / xmr…" className={field} />
          </label>
          <input value={payoutAddress} onChange={(e) => setPayoutAddress(e.target.value)} placeholder="your payout address for that currency" className={field} />
          <button onClick={publish} disabled={busy || !workerName || !payoutAddress} className="rounded-lg bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-500 disabled:opacity-50">
            {busy ? "Publishing…" : "Verify & publish"}
          </button>
        </section>
      )}

      {msg && <p className="text-sm text-red-400">{msg}</p>}
    </div>
  );
}
