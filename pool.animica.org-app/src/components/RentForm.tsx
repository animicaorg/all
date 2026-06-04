"use client";

import { useState } from "react";

type Coins = "ANM" | "XMR" | "BOTH";

export function RentForm({
  rigId,
  pricePerHourUsd,
  supportedCoins,
}: {
  rigId: string;
  pricePerHourUsd: string;
  supportedCoins: Coins;
}) {
  const [hours, setHours] = useState(2);
  const [coins, setCoins] = useState<Coins>(supportedCoins === "BOTH" ? "ANM" : supportedCoins);
  const [anmMode, setAnmMode] = useState<"pps" | "solo">("pps");
  const [anmAddr, setAnmAddr] = useState("");
  const [xmrAddr, setXmrAddr] = useState("");
  const [refundAddr, setRefundAddr] = useState("");
  const [refundCcy, setRefundCcy] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const coinChoices: Coins[] = supportedCoins === "BOTH" ? ["ANM", "XMR", "BOTH"] : [supportedCoins];
  const wantsAnm = coins === "ANM" || coins === "BOTH";
  const wantsXmr = coins === "XMR" || coins === "BOTH";
  const total = (Number(pricePerHourUsd) * hours).toFixed(2);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await fetch("/app/api/rentals", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          rigId,
          hours,
          coins,
          anmMode: wantsAnm ? anmMode : undefined,
          renterAnmAddress: anmAddr || undefined,
          renterXmrAddress: wantsXmr ? xmrAddr : undefined,
          renterRefundAddress: refundAddr || undefined,
          renterRefundCurrency: refundCcy || undefined,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Could not create rental");
        return;
      }
      // Send the renter to the NOWPayments invoice, then to their rental page.
      if (data.invoiceUrl) window.location.href = data.invoiceUrl;
      else window.location.href = `/app/rental/${data.rentalId}`;
    } finally {
      setBusy(false);
    }
  }

  const field = "w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2";

  return (
    <form onSubmit={submit} className="space-y-4 rounded-xl border border-white/10 bg-white/5 p-5">
      <label className="block text-sm">
        Hours
        <input
          type="number"
          min={1}
          max={720}
          value={hours}
          onChange={(e) => setHours(Math.max(1, Number(e.target.value)))}
          className={field}
        />
      </label>

      <label className="block text-sm">
        Mine
        <select value={coins} onChange={(e) => setCoins(e.target.value as Coins)} className={field}>
          {coinChoices.map((c) => (
            <option key={c} value={c}>
              {c === "BOTH" ? "ANM + Monero" : c === "ANM" ? "ANM" : "Monero (XMR)"}
            </option>
          ))}
        </select>
      </label>

      {wantsAnm && (
        <>
          <label className="block text-sm">
            ANM mode
            <select value={anmMode} onChange={(e) => setAnmMode(e.target.value as "pps" | "solo")} className={field}>
              <option value="pps">PPS (steady)</option>
              <option value="solo">Solo (block lottery)</option>
            </select>
          </label>
        </>
      )}

      <label className="block text-sm">
        Your Animica address (anim1…) — pool credit handle
        <input value={anmAddr} onChange={(e) => setAnmAddr(e.target.value)} placeholder="anim1…" className={field} />
      </label>

      {wantsXmr && (
        <label className="block text-sm">
          Your Monero payout address
          <input value={xmrAddr} onChange={(e) => setXmrAddr(e.target.value)} placeholder="4… or 8…" className={field} />
        </label>
      )}

      <details className="text-sm text-white/60">
        <summary className="cursor-pointer">Refund address (optional, for under-delivery)</summary>
        <div className="mt-2 space-y-2">
          <input value={refundAddr} onChange={(e) => setRefundAddr(e.target.value)} placeholder="refund crypto address" className={field} />
          <input value={refundCcy} onChange={(e) => setRefundCcy(e.target.value)} placeholder="refund currency e.g. usdttrc20" className={field} />
        </div>
      </details>

      {error && <p className="text-sm text-red-400">{error}</p>}

      <div className="flex items-center justify-between border-t border-white/10 pt-4">
        <span className="text-white/70">Total: ${total}</span>
        <button disabled={busy} className="rounded-lg bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-500 disabled:opacity-50">
          {busy ? "Creating…" : "Rent & pay"}
        </button>
      </div>
    </form>
  );
}
