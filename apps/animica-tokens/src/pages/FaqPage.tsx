export function FaqPage() {
  return (
    <section className="stack-lg">
      <article className="card">
        <h2>FAQ</h2>
        <h3>What token standard is used?</h3>
        <p>All launches use `contracts/standards/animica_token`.</p>
        <h3>What AMM contracts are used?</h3>
        <p>The DEX stack uses `animica_dex_pair`, `animica_dex_factory`, and `animica_dex_router` standards.</p>
        <h3>Where is media stored?</h3>
        <p>Uploads are validated, then pinned via IPFS provider when configured, with local CID fallback for dev mode.</p>
        <h3>How are factory/router addresses configured?</h3>
        <p>Run `scripts/animica_tokens/chain_ops.py deploy-stack` to deploy and auto-write env values into the app.</p>
      </article>
    </section>
  );
}
