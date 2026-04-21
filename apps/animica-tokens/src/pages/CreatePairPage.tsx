import { useMutation, useQuery } from "@tanstack/react-query";
import { FormEvent, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { createPool, fetchTokens } from "../lib/api";
import { getWalletAccounts } from "../lib/wallet";

export function CreatePairPage() {
  const [params] = useSearchParams();
  const tokensQ = useQuery({ queryKey: ["pair-tokens"], queryFn: () => fetchTokens("") });

  const [tokenA, setTokenA] = useState(params.get("tokenA") || "ANM");
  const [tokenB, setTokenB] = useState(params.get("tokenB") || "");
  const [feeBps, setFeeBps] = useState(30);
  const [metadataUri, setMetadataUri] = useState("");
  const [creator, setCreator] = useState("");
  const [status, setStatus] = useState("");

  const choices = useMemo(() => {
    return [
      { value: "ANM", label: "ANM" },
      ...(tokensQ.data ?? []).map((t) => ({ value: t.address, label: `${t.symbol} (${t.name})` }))
    ];
  }, [tokensQ.data]);

  const createM = useMutation({
    mutationFn: async () => {
      let c = creator;
      if (!c) {
        const acc = await getWalletAccounts();
        c = acc[0] || "";
      }
      if (!c) throw new Error("Creator address is required.");
      return createPool({
        tokenA,
        tokenB,
        feeBps,
        metadataUri,
        creatorAddress: c
      });
    },
    onSuccess(data) {
      setStatus(`Pair created: ${data.pool.pairAddress}`);
    },
    onError(err) {
      setStatus((err as Error).message);
    }
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    createM.mutate();
  };

  return (
    <section className="stack-lg">
      <div className="card">
        <h2>Create Pair</h2>
      </div>
      <form className="card form-grid" onSubmit={onSubmit}>
        <label>
          Token A
          <select value={tokenA} onChange={(e) => setTokenA(e.target.value)}>
            {choices.map((choice) => (
              <option key={choice.value} value={choice.value}>{choice.label}</option>
            ))}
          </select>
        </label>

        <label>
          Token B
          <select value={tokenB} onChange={(e) => setTokenB(e.target.value)}>
            <option value="">Select token</option>
            {choices.filter((c) => c.value !== tokenA).map((choice) => (
              <option key={choice.value} value={choice.value}>{choice.label}</option>
            ))}
          </select>
        </label>

        <label>
          Fee (bps)
          <input type="number" min={1} max={300} value={feeBps} onChange={(e) => setFeeBps(Number(e.target.value))} />
        </label>

        <label>
          Creator Address
          <input value={creator} onChange={(e) => setCreator(e.target.value)} placeholder="anim1..." />
        </label>

        <label className="full-row">
          Pair Metadata URI (optional)
          <input value={metadataUri} onChange={(e) => setMetadataUri(e.target.value)} placeholder="ipfs://..." />
        </label>

        <button className="btn-primary" type="submit" disabled={createM.isPending || !tokenB}>
          {createM.isPending ? "Creating..." : "Create Pair"}
        </button>
      </form>
      {status ? <p className="status">{status}</p> : null}
    </section>
  );
}
