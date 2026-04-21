import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { fetchPool } from "../lib/api";
import { formatInt } from "../lib/format";

export function PoolDetailPage() {
  const { pairId = "" } = useParams();
  const poolQ = useQuery({ queryKey: ["pool", pairId], queryFn: () => fetchPool(pairId), enabled: !!pairId });

  if (poolQ.isLoading) return <p className="muted">Loading pool...</p>;
  if (!poolQ.data) return <p className="muted">Pool not found.</p>;

  const { pool, swaps, liquidity } = poolQ.data;

  return (
    <section className="stack-lg">
      <article className="card">
        <h2>
          Pool {pool.tokenA}/{pool.tokenB}
        </h2>
        <p className="mono tiny">Pair {pool.pairAddress}</p>
        <ul className="list-clean">
          <li>Reserve A: {formatInt(pool.reserveA)}</li>
          <li>Reserve B: {formatInt(pool.reserveB)}</li>
          <li>LP Supply: {formatInt(pool.lpSupply)}</li>
          <li>Fee: {pool.feeBps} bps</li>
        </ul>
      </article>

      <div className="grid two">
        <section className="card">
          <h3>Recent Swaps</h3>
          <ul className="list-clean">
            {swaps.map((swap) => (
              <li key={swap.id}>
                {formatInt(swap.amountIn)} {swap.tokenIn} → {formatInt(swap.amountOut)} {swap.tokenOut}
              </li>
            ))}
            {swaps.length === 0 ? <li className="muted">No swaps yet.</li> : null}
          </ul>
        </section>

        <section className="card">
          <h3>Liquidity Activity</h3>
          <ul className="list-clean">
            {liquidity.map((ev) => (
              <li key={ev.id}>
                {ev.kind === "add" ? "Add" : "Remove"} {formatInt(ev.amountA)} / {formatInt(ev.amountB)}
              </li>
            ))}
            {liquidity.length === 0 ? <li className="muted">No liquidity activity yet.</li> : null}
          </ul>
        </section>
      </div>
    </section>
  );
}
