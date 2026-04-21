import { Link } from "react-router-dom";
import { shortAddr } from "../lib/format";
import type { TokenRecord } from "../lib/types";

export function TokenCard({ token }: { token: TokenRecord }) {
  return (
    <article className="card token-card">
      <div className="token-row">
        {token.imageUri ? <img src={token.imageUri} alt={token.symbol} className="token-logo" /> : <div className="token-logo placeholder" />}
        <div>
          <h3>{token.name}</h3>
          <p>{token.symbol}</p>
        </div>
      </div>
      <p className="muted line-clamp-2">{token.description || "No description"}</p>
      <p className="mono tiny">{shortAddr(token.address, 10)}</p>
      <div className="card-actions">
        <Link to={`/tokens/${encodeURIComponent(token.id)}`} className="btn-link">
          Token Page
        </Link>
        <Link to={`/dex/swap?tokenIn=ANM&tokenOut=${encodeURIComponent(token.address)}`} className="btn-link">
          Buy / Sell
        </Link>
      </div>
    </article>
  );
}
