import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { fetchToken, reportToken } from "../lib/api";
import { formatInt, shortAddr } from "../lib/format";
import { getWalletAccounts } from "../lib/wallet";

export function TokenDetailPage() {
  const { tokenId = "" } = useParams();
  const tokenQ = useQuery({
    queryKey: ["token", tokenId],
    queryFn: () => fetchToken(tokenId),
    enabled: !!tokenId
  });

  const reportM = useMutation({
    mutationFn: async () => {
      const acc = await getWalletAccounts();
      const reporter = acc[0] || "anonymous";
      return reportToken(tokenId, "user_report", reporter);
    }
  });

  if (tokenQ.isLoading) return <p className="muted">Loading token...</p>;
  if (!tokenQ.data) return <p className="muted">Token not found.</p>;

  const { token, swaps, liquidity } = tokenQ.data;

  return (
    <section className="stack-lg">
      <article className="card token-hero">
        {token.imageUri ? <img src={token.imageUri} alt={token.symbol} className="token-hero-logo" /> : null}
        <div>
          <h2>
            {token.name} ({token.symbol})
          </h2>
          <p className="mono tiny">{token.address}</p>
          <p>{token.description || "No description"}</p>
          <div className="token-links">
            {token.website ? <a href={token.website} target="_blank" rel="noreferrer">Website</a> : null}
            {token.twitter ? <a href={token.twitter} target="_blank" rel="noreferrer">Twitter</a> : null}
            {token.telegram ? <a href={token.telegram} target="_blank" rel="noreferrer">Telegram</a> : null}
            {token.discord ? <a href={token.discord} target="_blank" rel="noreferrer">Discord</a> : null}
            {token.github ? <a href={token.github} target="_blank" rel="noreferrer">GitHub</a> : null}
          </div>
          <div className="token-actions">
            <Link className="btn-primary" to={`/dex/swap?tokenIn=ANM&tokenOut=${encodeURIComponent(token.address)}`}>
              Trade
            </Link>
            <Link className="btn-secondary" to={`/create-pair?tokenA=ANM&tokenB=${encodeURIComponent(token.address)}`}>
              Create ANM Pair
            </Link>
            <button className="btn-ghost" onClick={() => reportM.mutate()}>
              {reportM.isPending ? "Reporting..." : "Report Token"}
            </button>
          </div>
        </div>
      </article>

      <div className="grid two">
        <section className="card">
          <h3>Token Stats</h3>
          <ul className="list-clean">
            <li>Total Supply: {formatInt(token.totalSupply || "0")}</li>
            <li>Max Supply: {formatInt(token.maxSupply || "0")}</li>
            <li>Decimals: {token.decimals}</li>
            <li>Creator: {shortAddr(token.creator, 12)}</li>
            <li>Created: {new Date(token.createdAt).toLocaleString()}</li>
            <li>Metadata URI: <span className="mono tiny">{token.metadataUri}</span></li>
          </ul>
        </section>

        <section className="card">
          <h3>Recent Activity</h3>
          <ul className="list-clean">
            {swaps.slice(0, 10).map((s) => (
              <li key={s.id}>
                Swap {formatInt(s.amountIn)} {s.tokenIn} → {formatInt(s.amountOut)} {s.tokenOut}
              </li>
            ))}
            {liquidity.slice(0, 10).map((l) => (
              <li key={l.id}>
                {l.kind === "add" ? "Added" : "Removed"} liquidity {formatInt(l.amountA)} / {formatInt(l.amountB)}
              </li>
            ))}
            {swaps.length === 0 && liquidity.length === 0 ? <li className="muted">No activity yet.</li> : null}
          </ul>
        </section>
      </div>
    </section>
  );
}
