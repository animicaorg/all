"use client";

import { useState } from "react";
import { useFormState, useFormStatus } from "react-dom";
import { startPurchaseAction, type PurchaseState } from "./actions";

const PRESETS = ["10", "25", "50", "100"];
const initial: PurchaseState = {};

function Pay({ amount }: { amount: string }) {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      disabled={pending || !amount || Number(amount) <= 0}
      className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
    >
      {pending ? "Starting checkout…" : `Pay $${amount || "0"} in crypto`}
    </button>
  );
}

export function BuyCredits() {
  const [state, action] = useFormState(startPurchaseAction, initial);
  const [amount, setAmount] = useState("25");

  return (
    <form action={action} className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {PRESETS.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => setAmount(p)}
            className={`rounded-lg border px-3 py-1.5 text-sm ${
              amount === p
                ? "border-blue-400 bg-blue-500/15 text-blue-200"
                : "border-white/10 bg-white/5 hover:border-white/30"
            }`}
          >
            ${p}
          </button>
        ))}
      </div>
      <label className="block text-sm">
        <span className="mb-1 block text-white/60">Amount (USD)</span>
        <input
          name="amountUsd"
          inputMode="decimal"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          className="w-40 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm outline-none focus:border-blue-400"
        />
      </label>
      {state.error && <p className="text-sm text-red-400">{state.error}</p>}
      <Pay amount={amount} />
      <p className="text-xs text-white/40">
        You’ll be redirected to NOWPayments to pay in BTC, ETH, USDT, XMR, and more.
        Credit is added automatically once the payment confirms.
      </p>
    </form>
  );
}
