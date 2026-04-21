import { useMutation, useQuery } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { addLiquidity, fetchPools, removeLiquidity } from "../lib/api";
import { nowPlusBlocks } from "../lib/format";

export function PoolsPage() {
  const poolsQ = useQuery({ queryKey: ["pools"], queryFn: fetchPools });
  const [pairId, setPairId] = useState("");
  const [amountA, setAmountA] = useState("0");
  const [amountB, setAmountB] = useState("0");
  const [lpAmount, setLpAmount] = useState("0");
  const [provider, setProvider] = useState("");
  const [status, setStatus] = useState("");

  const addM = useMutation({
    mutationFn: async () =>
      addLiquidity({
        pairId,
        amountA,
        amountB,
        providerAddress: provider,
        deadline: nowPlusBlocks(600)
      }),
    onSuccess() {
      setStatus("Liquidity add submitted.");
      poolsQ.refetch();
    },
    onError(err) {
      setStatus((err as Error).message);
    }
  });

  const removeM = useMutation({
    mutationFn: async () =>
      removeLiquidity({
        pairId,
        lpAmount,
        providerAddress: provider,
        minAmountA: 0,
        minAmountB: 0,
        deadline: nowPlusBlocks(600)
      }),
    onSuccess() {
      setStatus("Liquidity remove submitted.");
      poolsQ.refetch();
    },
    onError(err) {
      setStatus((err as Error).message);
    }
  });

  const onAdd = (e: FormEvent) => {
    e.preventDefault();
    addM.mutate();
  };

  const onRemove = (e: FormEvent) => {
    e.preventDefault();
    removeM.mutate();
  };

  return (
    <section className="stack-lg">
      <div className="card">
        <h2>Pools</h2>
      </div>

      <div className="grid two">
        <section className="card">
          <h3>Pool List</h3>
          <ul className="list-clean">
            {(poolsQ.data ?? []).map((pool) => (
              <li key={pool.id}>
                <Link to={`/dex/pools/${encodeURIComponent(pool.id)}`}>
                  {pool.tokenA}/{pool.tokenB} · fee {pool.feeBps} bps
                </Link>
              </li>
            ))}
          </ul>
        </section>

        <section className="card stack-md">
          <h3>LP Actions</h3>
          <form className="stack-sm" onSubmit={onAdd}>
            <label>
              Pair ID
              <input value={pairId} onChange={(e) => setPairId(e.target.value)} required />
            </label>
            <label>
              Provider Address
              <input value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="anim1..." required />
            </label>
            <label>
              Amount A
              <input value={amountA} onChange={(e) => setAmountA(e.target.value)} required />
            </label>
            <label>
              Amount B
              <input value={amountB} onChange={(e) => setAmountB(e.target.value)} required />
            </label>
            <button className="btn-primary" type="submit" disabled={addM.isPending}>
              {addM.isPending ? "Adding..." : "Add Liquidity"}
            </button>
          </form>

          <form className="stack-sm" onSubmit={onRemove}>
            <label>
              LP Amount
              <input value={lpAmount} onChange={(e) => setLpAmount(e.target.value)} required />
            </label>
            <button className="btn-secondary" type="submit" disabled={removeM.isPending}>
              {removeM.isPending ? "Removing..." : "Remove Liquidity"}
            </button>
          </form>
        </section>
      </div>
      {status ? <p className="status">{status}</p> : null}
    </section>
  );
}
