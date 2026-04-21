import { useMutation, useQuery } from "@tanstack/react-query";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { fetchTokens, quote, swapExactIn } from "../lib/api";
import { nowPlusBlocks } from "../lib/format";
import { getWalletAccounts } from "../lib/wallet";

export function SwapPage() {
  const [params] = useSearchParams();
  const tokensQ = useQuery({ queryKey: ["swap-tokens"], queryFn: () => fetchTokens("") });

  const [tokenIn, setTokenIn] = useState(params.get("tokenIn") || "ANM");
  const [tokenOut, setTokenOut] = useState(params.get("tokenOut") || "");
  const [amountIn, setAmountIn] = useState("1");
  const [minOut, setMinOut] = useState("0");
  const [recipient, setRecipient] = useState("");
  const [quoteOut, setQuoteOut] = useState("0");
  const [status, setStatus] = useState("");

  useEffect(() => {
    void getWalletAccounts().then((acc) => {
      if (!recipient) setRecipient(acc[0] || "");
    });
  }, [recipient]);

  const tokenChoices = useMemo(() => {
    const base = [{ label: "ANM", value: "ANM" }];
    const others = (tokensQ.data ?? []).map((t) => ({ label: `${t.symbol} (${t.name})`, value: t.address }));
    return [...base, ...others];
  }, [tokensQ.data]);

  const quoteM = useMutation({
    mutationFn: async () => {
      const q = await quote({
        tokenIn,
        tokenOut,
        amountIn,
        mode: "exactIn"
      });
      if (!q.ok) throw new Error(q.error || "quote failed");
      return q;
    },
    onSuccess(data) {
      setQuoteOut(data.amountOut || "0");
      if (!minOut || minOut === "0") {
        setMinOut(data.amountOut || "0");
      }
      setStatus("Quote updated.");
    },
    onError(err) {
      setStatus((err as Error).message);
    }
  });

  const swapM = useMutation({
    mutationFn: async () => {
      if (!recipient) throw new Error("Recipient address is required.");
      const swap = await swapExactIn({
        tokenIn,
        tokenOut,
        amountIn,
        minAmountOut: minOut,
        traderAddress: recipient,
        toAddress: recipient,
        deadline: nowPlusBlocks(600)
      });
      return swap;
    },
    onSuccess(data) {
      setStatus(`Swap submitted: ${data.swap.txHash || "tx pending"}`);
    },
    onError(err) {
      setStatus((err as Error).message);
    }
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    swapM.mutate();
  };

  return (
    <section className="stack-lg">
      <div className="card">
        <h2>Swap</h2>
        <p className="muted">Direct-pair routing with slippage + deadline checks.</p>
      </div>
      <form className="card form-grid" onSubmit={onSubmit}>
        <label>
          Token In
          <select value={tokenIn} onChange={(e) => setTokenIn(e.target.value)}>
            {tokenChoices.map((token) => (
              <option key={token.value} value={token.value}>
                {token.label}
              </option>
            ))}
          </select>
        </label>

        <label>
          Token Out
          <select value={tokenOut} onChange={(e) => setTokenOut(e.target.value)}>
            <option value="">Select token</option>
            {tokenChoices
              .filter((token) => token.value !== tokenIn)
              .map((token) => (
                <option key={token.value} value={token.value}>
                  {token.label}
                </option>
              ))}
          </select>
        </label>

        <label>
          Amount In
          <input value={amountIn} onChange={(e) => setAmountIn(e.target.value)} />
        </label>

        <label>
          Min Amount Out
          <input value={minOut} onChange={(e) => setMinOut(e.target.value)} />
        </label>

        <label>
          Recipient
          <input value={recipient} onChange={(e) => setRecipient(e.target.value)} placeholder="anim1..." />
        </label>

        <div className="full-row inline-actions">
          <button className="btn-secondary" type="button" onClick={() => quoteM.mutate()} disabled={!tokenOut || quoteM.isPending}>
            {quoteM.isPending ? "Quoting..." : "Get Quote"}
          </button>
          <span className="muted">Estimated out: {quoteOut}</span>
        </div>

        <button className="btn-primary" type="submit" disabled={swapM.isPending || !tokenOut}>
          {swapM.isPending ? "Swapping..." : "Swap Exact In"}
        </button>
      </form>
      {status ? <p className="status">{status}</p> : null}
    </section>
  );
}
