import type { Metadata } from 'next';
import { prisma } from '@/lib/db';
import { Code, K, Callout, PageNav } from '../doc';

// Live catalog of the deployed example functions: everything on this page — slugs, versions,
// capabilities, execution counts, anchor txids and the source itself — is read from the
// database rows the deploy pipeline wrote. Nothing is hardcoded.
export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: 'Examples — Animica Python Cloud',
  description: 'Six working, deployed example functions with source, live endpoints and anchor txids.',
};

// The designated examples account (created/maintained by scripts/cloud-examples.ts).
const EXAMPLES_ADDRESS = 'anim1examplesdev0cloud0demo0acct0000';

export default async function Page() {
  const owner = await prisma.account.findUnique({
    where: { address: EXAMPLES_ADDRESS },
    select: { id: true, handle: true, address: true },
  });
  const fns = owner
    ? await prisma.cloudFunction.findMany({
        where: { ownerId: owner.id, status: 'PUBLISHED', suspendedAt: null },
        orderBy: { createdAt: 'asc' },
        select: {
          id: true,
          slug: true,
          name: true,
          description: true,
          capabilities: true,
          perCallNanm: true,
          timeoutMs: true,
          memoryMb: true,
          currentVersion: true,
          versions: { orderBy: { version: 'desc' }, take: 5, select: { version: true, source: true, packages: true } },
          deployments: {
            where: { status: 'ACTIVE' },
            orderBy: { activatedAt: 'desc' },
            take: 1,
            select: { anchorTxid: true, daBlobId: true, anchorConfirms: true },
          },
          _count: { select: { executions: true } },
        },
      })
    : [];
  const seg = owner?.handle ?? owner?.address ?? 'examples';

  return (
    <>
      <h1>Examples</h1>
      <p className="cd-lead">
        Six working functions, deployed on this platform through the real pipeline by{' '}
        <K>scripts/cloud-examples.ts</K> (repository: <K>examples/</K>, one directory per example with
        source and a README). Everything below — versions, capabilities, anchors, execution counts and the
        source itself — is live data.
      </p>

      {fns.length === 0 ? (
        <div className="empty">
          The examples are not deployed on this instance yet. Run{' '}
          <span className="mono">npx tsx scripts/cloud-examples.ts</span> to deploy and execute all six.
        </div>
      ) : (
        fns.map((fn) => {
          const ver = fn.versions.find((v) => v.version === fn.currentVersion) ?? fn.versions[0];
          const dep = fn.deployments[0];
          return (
            <section key={fn.id} className="panel" style={{ marginTop: 18 }}>
              <h2 style={{ marginTop: 0 }}>
                {fn.name} <span className="pill mono">v{fn.currentVersion}</span>
              </h2>
              <p>{fn.description}</p>
              <p className="mono" style={{ fontSize: 12.5, wordBreak: 'break-all' }}>
                POST /api/cloud/v1/fn/{seg}/{fn.slug}
              </p>
              <p style={{ fontSize: 13 }}>
                {fn.capabilities.length ? (
                  <>
                    capabilities: {fn.capabilities.map((c) => (
                      <span key={c} className="pill" style={{ marginRight: 6 }}>
                        {c}
                      </span>
                    ))}
                  </>
                ) : (
                  <span className="muted">no capabilities</span>
                )}
                {ver?.packages?.length ? <> · packages: {ver.packages.join(', ')}</> : null}
                {fn.perCallNanm > 0n ? (
                  <>
                    {' '}
                    · surcharge <b>{fn.perCallNanm.toString()} nANM</b>/call
                  </>
                ) : null}
              </p>
              <p className="muted" style={{ fontSize: 12.5 }}>
                {fn._count.executions} execution{fn._count.executions === 1 ? '' : 's'} recorded · timeout{' '}
                {fn.timeoutMs / 1000}s · {fn.memoryMb} MB
                {dep?.anchorTxid ? (
                  <>
                    {' '}
                    · anchored on-chain:{' '}
                    <span className="mono" style={{ wordBreak: 'break-all' }}>
                      {dep.anchorTxid}
                    </span>{' '}
                    ({dep.anchorConfirms} confirmations)
                  </>
                ) : (
                  <> · unanchored (recorded reason in the deployment log)</>
                )}
              </p>
              {ver ? (
                <details>
                  <summary style={{ cursor: 'pointer', color: 'var(--text-dim)', fontSize: 13.5, minHeight: 40, display: 'flex', alignItems: 'center' }}>
                    view the deployed source (v{ver.version}, exactly what the sandbox runs)
                  </summary>
                  <Code title={`${fn.slug}/handler.py`}>{ver.source}</Code>
                </details>
              ) : null}
            </section>
          );
        })
      )}

      <h2>Try one now</h2>
      <Code title="anonymous free-tier call (hello-api)">{`curl -s https://animica.dev/api/cloud/v1/fn/${seg}/hello-api \\
  -H 'content-type: application/json' -d '{"name": "Ada"}'`}</Code>

      <Callout>
        The AI examples (<K>ai-summarizer</K>, <K>chain-pulse</K>) call <K>animica.ai.infer</K> and report
        an <K>engine</K> field: <K>animica-ai</K> when the miner network served the inference, or an
        honest deterministic fallback when it could not — so the endpoints stay useful and truthful either
        way. That pattern is documented on the <a href="/docs/cloud/ai">AI page</a>.
      </Callout>
      <PageNav current="/docs/cloud/examples" />
    </>
  );
}
