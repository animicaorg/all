export function DocsPage() {
  return (
    <section className="stack-lg">
      <article className="card">
        <h2>Animica Tokens Docs</h2>
        <p className="muted">
          Contracts: <code>contracts/standards/animica_token</code>, <code>animica_dex_factory</code>, <code>animica_dex_router</code>, <code>animica_dex_pair</code>
        </p>
      </article>
      <article className="card">
        <h3>Operations</h3>
        <p className="muted">
          Use <code>scripts/animica_tokens/chain_ops.py</code> to deploy stack contracts, launch tokens, create pairs, add/remove liquidity, and execute swaps.
        </p>
      </article>
      <article className="card">
        <h3>API + Website</h3>
        <p className="muted">
          The unified launcher + DEX UI uses the server APIs under <code>/api</code> and persists metadata/media with the local IPFS-style store and optional Pinata pinning.
        </p>
      </article>
    </section>
  );
}
