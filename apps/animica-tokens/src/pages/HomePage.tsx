import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchPools, fetchStats, fetchTokens } from "../lib/api";
import { formatInt } from "../lib/format";
import { StatPill } from "../components/StatPill";

export function HomePage() {
  const statsQ = useQuery({ queryKey: ["stats"], queryFn: fetchStats });
  const tokensQ = useQuery({ queryKey: ["tokens-home"], queryFn: () => fetchTokens("") });
  const poolsQ = useQuery({ queryKey: ["pools-home"], queryFn: fetchPools });

  return (
    <section className="stack-lg">
      <div className="card hero">
        <h2>Launch, list, and trade VM-PY tokens in one place</h2>
        <p>
          Upload media to IPFS, deploy standardized token contracts, create pools,
          and trade on Animica-native AMM contracts.
        </p>
        <div className="hero-actions">
          <Link className="btn-primary" to="/launch">
            Launch Token
          </Link>
          <Link className="btn-secondary" to="/dex/swap">
            Open Swap
          </Link>
        </div>
      </div>

      <div className="stats-grid">
        <StatPill label="Tokens">{formatInt(statsQ.data?.tokenCount ?? 0)}</StatPill>
        <StatPill label="Pools">{formatInt(statsQ.data?.poolCount ?? 0)}</StatPill>
        <StatPill label="24h Swaps">{formatInt(statsQ.data?.swapCount24h ?? 0)}</StatPill>
        <StatPill label="Liquidity">{formatInt(statsQ.data?.liquidityNotional ?? "0")}</StatPill>
      </div>

      <div className="grid two">
        <section className="card">
          <h3>New Tokens</h3>
          <ul className="list-clean">
            {(tokensQ.data ?? []).slice(0, 8).map((token) => (
              <li key={token.id}>
                <Link to={`/tokens/${encodeURIComponent(token.id)}`}>
                  {token.symbol} · {token.name}
                </Link>
              </li>
            ))}
            {tokensQ.isLoading ? <li className="muted">Loading tokens...</li> : null}
          </ul>
        </section>

        <section className="card">
          <h3>Active Pools</h3>
          <ul className="list-clean">
            {(poolsQ.data ?? []).slice(0, 8).map((pool) => (
              <li key={pool.id}>
                <Link to={`/dex/pools/${encodeURIComponent(pool.id)}`}>
                  {pool.tokenA}/{pool.tokenB} · fee {pool.feeBps} bps
                </Link>
              </li>
            ))}
            {poolsQ.isLoading ? <li className="muted">Loading pools...</li> : null}
          </ul>
        </section>
      </div>
    </section>
  );
}
