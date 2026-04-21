import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { TokenCard } from "../components/TokenCard";
import { fetchTokens } from "../lib/api";

export function TokensPage() {
  const [q, setQ] = useState("");
  const tokensQ = useQuery({
    queryKey: ["tokens", q],
    queryFn: () => fetchTokens(q)
  });

  return (
    <section className="stack-lg">
      <div className="card">
        <h2>Discover Tokens</h2>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search by name, symbol, address"
        />
      </div>

      <div className="grid three">
        {(tokensQ.data ?? []).map((token) => (
          <TokenCard key={token.id} token={token} />
        ))}
      </div>
      {tokensQ.isLoading ? <p className="muted">Loading tokens...</p> : null}
      {!tokensQ.isLoading && (tokensQ.data?.length ?? 0) === 0 ? <p className="muted">No tokens found.</p> : null}
    </section>
  );
}
