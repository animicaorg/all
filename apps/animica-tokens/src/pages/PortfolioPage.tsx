import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { fetchPortfolio } from "../lib/api";
import { formatInt, shortAddr } from "../lib/format";

export function PortfolioPage() {
  const [address, setAddress] = useState("");

  const portfolioQ = useQuery({
    queryKey: ["portfolio", address],
    queryFn: () => fetchPortfolio(address),
    enabled: !!address
  });

  return (
    <section className="stack-lg">
      <div className="card">
        <h2>Portfolio</h2>
        <label>
          Address
          <input
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="anim1..."
          />
        </label>
      </div>

      {!address ? <p className="muted">Enter an address to view portfolio aggregates.</p> : null}
      {portfolioQ.isLoading ? <p className="muted">Loading portfolio...</p> : null}

      {portfolioQ.data ? (
        <>
          <div className="grid two">
            <section className="card">
              <h3>Created Tokens</h3>
              <ul className="list-clean">
                {portfolioQ.data.createdTokens.map((token) => (
                  <li key={token.id}>
                    {token.symbol} · {token.name} ({shortAddr(token.address, 10)})
                  </li>
                ))}
                {portfolioQ.data.createdTokens.length === 0 ? <li className="muted">No created tokens.</li> : null}
              </ul>
            </section>

            <section className="card">
              <h3>LP Positions</h3>
              <ul className="list-clean">
                {portfolioQ.data.lpPositions.map((pos) => (
                  <li key={`${pos.pairId}-${pos.pairAddress}`}>
                    {pos.tokenA}/{pos.tokenB} · LP {formatInt(pos.lpAmount)} · Share {pos.shareBps / 100}%
                  </li>
                ))}
                {portfolioQ.data.lpPositions.length === 0 ? <li className="muted">No LP positions.</li> : null}
              </ul>
            </section>
          </div>

          <section className="card">
            <h3>Recent Activity</h3>
            <ul className="list-clean">
              {portfolioQ.data.recentActivity.map((entry) => (
                <li key={entry.id} className="mono tiny">
                  {JSON.stringify(entry)}
                </li>
              ))}
              {portfolioQ.data.recentActivity.length === 0 ? <li className="muted">No recent activity.</li> : null}
            </ul>
          </section>
        </>
      ) : null}
    </section>
  );
}
