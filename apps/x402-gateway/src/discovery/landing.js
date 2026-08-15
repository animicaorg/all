'use strict';
/**
 * The human-and-crawler face of the gateway: GET /x402 with
 * `Accept: text/html`. Everything on it is RENDERED FROM THE LIVE REGISTRY
 * at request time — prices, availability, endpoints, input schemas and the
 * entropy disclosure all come from the catalog object the machine surfaces
 * return, so the page cannot advertise a price nobody charges or a product
 * that is currently refusing traffic.
 *
 * Three rules this file exists to keep:
 *
 *   1. No second copy of a price. Money strings come from the catalog entry
 *      (which comes from the product object, which is where the config lands).
 *   2. No claim the responses do not make. The randomness copy states the
 *      current entropy source verbatim from the gateway's own readiness draw
 *      (software CSPRNG, attestation.attested=false today) and never says
 *      hardware attestation is live. The response examples are REAL captures
 *      (src/discovery/samples.js), not invented JSON.
 *   3. No advertisement of the development echo: devOnly products are
 *      filtered out here even when the catalog still lists them (dev mode).
 *
 * Self-contained by construction: inline CSS, no scripts, no external
 * requests. Palette is the house one — #2E63FF for the buyer's own actions,
 * #14C79B for network truth — with a dark-mode variant and AA contrast in
 * both (green is never a background under white text).
 */

const { buildPaymentRequiredForRoute } = require('../middleware');
const { SAMPLES, CAPTURED_AT } = require('./samples');
const { links, identity, networkFacts, networkDisplayName, anchorFor, CONTACT, REPO, HOMEPAGE, VERIFIER_URL } = require('./links');

/** Display order: the hero product first, then its family, then the rest. */
const ORDER = [
  'qrng', 'random_int', 'random_shuffle', 'random_pick', 'random_bulk', 'random_commit',
  'bulk_chain', 'chain_address_history', 'chain_batch_balances',
  'priority_inference',
];

const GROUPS = [
  {
    id: 'randomness',
    title: 'Verifiable randomness',
    ids: ['qrng', 'random_int', 'random_shuffle', 'random_pick', 'random_bulk', 'random_commit'],
    blurb:
      'One node draw per request, delivered with the node\'s own signature over it and — for the derived products — the exact rules to recompute the result yourself.',
    // The proof-verification link the discovery spec asks for: the actual
    // zero-dependency file the responses name in their `verification` block,
    // not a page about it.
    verifier: true,
  },
  {
    id: 'chain-data',
    title: 'Bulk post-quantum L1 chain data',
    ids: ['bulk_chain', 'chain_address_history', 'chain_batch_balances'],
    blurb:
      'The public Animica APIs stay free and unmetered. What x402 buys here is range exports, batching, an account-history index that the free APIs do not have, and hard caps you can plan against.',
  },
  {
    id: 'inference',
    title: 'Priority AI inference',
    ids: ['priority_inference'],
    blurb:
      'Sold only while enough community GPUs are live-serving the tier. Below the floor the catalog says unavailable, the endpoint answers 503, and no payment is ever requested.',
  },
];

/**
 * Per-product copy that the product descriptions do not carry: what a buyer
 * can actually check about the answer. Keyed by product id; a product with
 * no entry simply gets no verifiability paragraph (never an invented one).
 */
const VERIFIABILITY = {
  qrng:
    'Each response carries <code>attestation</code>: the node computes <code>digest_hex = sha3_256(bytes(randomness))</code> and signs those 32 digest bytes with its ed25519 key, whose public half is in the response. Recompute the digest, verify the signature, and you know the serving node signed exactly the bytes you were given. That is a signature over the answer, not a proof of where the entropy came from — the source fields below tell you that part, and they are published free in the catalog before you pay.',
  random_int:
    'Recomputable end to end. The response publishes the raw draw, the domain string, the seed derivation and the rejection-sampling rule, so anyone can re-derive the same integers from the same draw — <code>verify.js</code> in the Animica repo does it with no dependencies.',
  random_shuffle:
    'Recomputable end to end: the permutation is derived from the published draw with the published Fisher-Yates rule, so a third party can replay it and get the same ordering.',
  random_pick:
    'Recomputable end to end: indices (and weights, when used) are derived from the published draw with the published sampling rule.',
  random_bulk:
    'Every draw in the batch carries its own signed attestation; one settlement, N independently checkable draws.',
  random_commit:
    'The paid call returns a commitment; the disclosure is a FREE public endpoint anyone can read. That proves the value existed before the reveal and was not swapped afterwards. It does NOT prove the operator could not have discarded a draw it disliked before committing — the response says so in its own words.',
  bulk_chain:
    'Nothing here is exclusive data: every block and transaction is re-checkable against the free public explorer REST API and the free node JSON-RPC. What you are buying is the range export, the format and the caps.',
  chain_address_history:
    'Served from an index this gateway builds itself by walking the chain, because no account-history index exists on the free APIs. Rows carry the block height and hash they came from, so any row can be re-verified against the free APIs. When the index falls behind its freshness gate the product reports unavailable rather than selling stale history.',
  chain_batch_balances:
    'Balances are the node\'s own confirmed balances, answered in one batched call and pinned to the head height reported in the response. Re-check any address against the free explorer API.',
  priority_inference:
    'The honest advantage is admission, routing and timeouts — not a different model and not reserved hardware. If the upstream still fails after your payment settles, you get a signed error receipt referencing the settled payment and an incident row is opened for reconciliation.',
};

const HTML_ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

function esc(value) {
  return String(value === undefined || value === null ? '' : value).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}

/** JSON for a <script type="application/ld+json"> block (no tag escapes). */
function jsonLd(obj) {
  return JSON.stringify(obj, null, 2).replace(/</g, '\\u003c').replace(/>/g, '\\u003e');
}

function pre(text, label) {
  return `<pre${label ? ` aria-label="${esc(label)}"` : ''}><code>${esc(text)}</code></pre>`;
}

function json(value) {
  return pre(JSON.stringify(value, null, 2));
}

/** Example curl for a product route, built from its real path and schema. */
function curlFor(entry, sample, base) {
  const url = `${base}${sample && sample.path ? sample.path : entry.path}`;
  const method = (sample && sample.method) || entry.method || 'GET';
  if (method === 'GET') return `curl -i "${url}"`;
  const body = sample && sample.body
    ? JSON.stringify(sample.body)
    : JSON.stringify(exampleBodyFrom(entry));
  return `curl -i -X ${method} "${url}" \\\n  -H 'content-type: application/json' \\\n  -d '${body}'`;
}

/** Field names from the product's own declared input schema — never invented. */
function exampleBodyFrom(entry) {
  const input = entry.outputSchema && entry.outputSchema.input;
  const fields = (input && input.bodyFields) || {};
  const out = {};
  for (const [name, spec] of Object.entries(fields)) {
    if (spec && spec.required) out[name] = `<${spec.type || 'value'}>`;
  }
  if (Object.keys(out).length === 0) {
    for (const [name, spec] of Object.entries(fields).slice(0, 2)) out[name] = `<${(spec && spec.type) || 'value'}>`;
  }
  return out;
}

function chip(text, kind) {
  return `<span class="chip chip-${kind}">${esc(text)}</span>`;
}

function availabilityChip(entry) {
  if (entry.available) return chip('available now', 'ok');
  return chip(`unavailable — ${entry.unavailable_reason || 'see catalog'}`, 'off');
}

/**
 * Availability copy. Products gated on something outside this gateway say so
 * in words as well as in the chip, and an unavailable product states the
 * live reason the registry gave — no product is ever presented as buyable
 * while its own availability hook says otherwise.
 */
function availabilityNote(entry) {
  const gated = entry.id === 'priority_inference'
    ? 'Available when network serving capacity permits. '
    : '';
  if (entry.available) {
    return gated
      ? `<p class="note">${gated}It is available right now.</p>`
      : '';
  }
  const reason = entry.unavailable_detail || entry.unavailable_reason || 'see the catalog for the current reason';
  return `<p class="note">${esc(gated)}<strong>Not available right now</strong> — ${esc(reason)}. While that is true this endpoint answers 503 and never asks for payment, and the catalog reports <code>available: false</code> with the reason <code>${esc(entry.unavailable_reason || 'unknown')}</code>.</p>`;
}

/** The one place a price is turned into display text. */
function priceText(entry) {
  return `$${entry.price} ${entry.currency} per request`;
}

function entropyBlock(entry) {
  const e = entry.entropy;
  if (!e) return '';
  if (!e.source) {
    return `<p class="note">Entropy source: not observed yet on this process. Every paid response carries the node's own <code>source</code>, <code>health</code> and <code>attestation</code> fields verbatim.</p>`;
  }
  const rows = [
    ['source', e.source],
    ['model', e.model],
    ['hardware', String(e.is_hardware)],
    ['quantum', String(e.is_quantum)],
    ['attested', String(e.attested)],
    ['signer backend', e.signer_backend],
    ['entropy health', e.health_passed === null ? 'unknown' : (e.health_passed ? 'passed' : 'FAILED')],
    ['min entropy/byte', e.min_entropy_per_byte === null ? 'unknown' : String(e.min_entropy_per_byte)],
    ['observed at', e.observed_at],
  ].filter(([, v]) => v !== null && v !== undefined && v !== '');
  return `<div class="disclosure">
      <h4>Live entropy disclosure (free, read before you pay)</h4>
      <table class="facts">${rows.map(([k, v]) => `<tr><th scope="row">${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')}</table>
      <p class="note">Read from this gateway's own readiness draw, not from copy. <strong>${e.is_quantum === false ? 'No hardware or quantum entropy source is connected today' : 'Source as reported by the node'}</strong>${e.attested === false ? ' and the signer is a software key, so <code>attestation.attested</code> is <code>false</code>' : ''}. These fields flip on their own if a hardware provider is ever connected — nothing on this page is hand-maintained.</p>
    </div>`;
}

function paymentBlockFor(entry, facts) {
  const shape = {
    network: facts.network_caip2,
    asset: facts.asset_address,
    amount_atomic: entry.price_atomic,
    price_usd: entry.price,
    payer: '<the address that signed the authorization>',
    settlement_tx: '<the Base transaction hash that moved the USDC>',
  };
  return `<p class="note">Every paid JSON response also carries a <code>payment</code> block, generated per request:</p>${json(shape)}`;
}

function productCard(entry, facts, base) {
  const sample = SAMPLES[entry.id];
  const parts = [];
  parts.push(`<article class="card" id="${esc(anchorFor(entry.id))}">`);
  parts.push(`<header class="card-head"><h3>${esc(entry.name)}</h3><div class="chips">${chip(priceText(entry), 'price')}${availabilityChip(entry)}</div></header>`);
  parts.push(`<p>${esc(entry.description)}</p>`);
  parts.push(availabilityNote(entry));
  parts.push(`<h4>Endpoints</h4><ul class="endpoints">${entry.endpoints
    .map((e) => `<li><code>${esc(e)}</code></li>`).join('')}</ul>`);
  if (entry.free_endpoints && entry.free_endpoints.length) {
    parts.push(`<p class="note">Free, unpaid part of this product: ${entry.free_endpoints
      .map((f) => `<code>${esc(f.endpoint)}</code>`).join(', ')} — ${esc(entry.free_endpoints[0].description || 'no payment required')}.</p>`);
  }
  parts.push('<h4>Example request</h4>');
  parts.push(pre(curlFor(entry, sample, base), `example request for ${entry.id}`));
  if (VERIFIABILITY[entry.id]) {
    parts.push(`<h4>What you can verify</h4><p>${VERIFIABILITY[entry.id]}</p>`);
  }
  parts.push(entropyBlock(entry));
  if (sample) {
    const status = sample.status && sample.status !== 200 ? ` (HTTP ${sample.status})` : '';
    parts.push(`<details><summary>Real response${status} — captured ${esc(CAPTURED_AT)}${sample.truncated ? `, ${esc(sample.truncated)}` : ''}</summary>${json(sample.response)}${sample.status && sample.status !== 200 ? '' : paymentBlockFor(entry, facts)}</details>`);
  }
  parts.push(`<p class="note">Machine description: <a href="${esc(base)}/.well-known/x402">catalog entry</a> · <a href="${esc(base)}/x402/openapi.json">OpenAPI</a></p>`);
  parts.push('</article>');
  return parts.join('\n');
}

/** The 402 a first request actually receives, built by the gateway's own code. */
function exampleChallenge(products, cfg) {
  const qrng = products.find((p) => p.id === 'qrng') || products.find((p) => !p.devOnly);
  if (!qrng) return null;
  try {
    return buildPaymentRequiredForRoute({
      path: qrng.path,
      priceUsd: qrng.priceUsd,
      description: qrng.description,
      mimeType: qrng.mimeType || 'application/json',
    }, cfg);
  } catch (e) {
    return null; // no configured payment lane on this deployment — say so
  }
}

function faqFor(catalog, facts) {
  const sellable = catalog.products.filter((p) => !p.development_only);
  const priceList = sellable.map((p) => `${p.name} (${p.path}) $${p.price}`).join('; ');
  const qrng = sellable.find((p) => p.id === 'qrng');
  const entropy = qrng && qrng.entropy;
  return [
    {
      q: 'Do I need an account, an API key or a subscription?',
      a: 'No. There is no signup, no key issuance and no per-customer state. The only credential is a payment authorization you sign locally and send in the request header; the gateway verifies and settles it, then answers.',
    },
    {
      q: 'What does a request cost, and in what?',
      a: `Prices are per request, quoted in USD and paid in ${facts.asset || 'the configured stablecoin'} on ${networkDisplayName(facts)} mainnet (CAIP-2 ${facts.network_caip2}, chain id ${facts.chain_id}). Current prices: ${priceList}.`,
    },
    {
      q: 'Is the randomness generated by quantum hardware?',
      a: entropy && entropy.is_quantum === false
        ? `Not today. The serving node is running its software-CSPRNG fallback: the free catalog and every paid response report source "${entropy.source}", is_quantum false and attested false. What is verifiable right now is the node's ed25519 signature over the sha3-256 digest of the exact bytes you received, and — for the derived products — full recomputation of the result from that draw. If a hardware provider is connected later, those same fields will say so; the copy is generated from them.`
        : 'The response fields source.is_quantum and attestation.attested report the truth per draw; read them in the free catalog before paying.',
    },
    {
      q: 'Are the free Animica APIs still free?',
      a: 'Yes. The public node JSON-RPC and the explorer REST API remain free and unmetered. The paid routes sell bulk, batching, indexes those APIs do not have, and per-request verifiable randomness — never access to something that was free yesterday.',
    },
    {
      q: 'What happens if my payment settles but the service then fails?',
      a: 'You get a signed, machine-readable error receipt that references the settled payment, and an incident row is opened on this gateway for reconciliation. Cheap read products are produced BEFORE settlement, so a failure there charges nothing at all.',
    },
    {
      q: 'How does the payment flow work?',
      a: 'Call the endpoint. It answers 402 with the exact terms (amount, asset, network, recipient, expiry). Your client signs an authorization for those terms locally and retries with it in the PAYMENT-SIGNATURE header. The gateway verifies it, settles it on-chain through its own facilitator, and returns the response with the settlement details in the PAYMENT-RESPONSE header.',
    },
    {
      q: 'Where is the machine-readable description?',
      a: `The catalog is at ${catalog.discovery.well_known} and ${catalog.discovery.catalog}; the OpenAPI 3.1 document is at ${catalog.discovery.openapi}; aggregate settlement stats are at ${catalog.discovery.stats}.`,
    },
  ];
}

const CSS = `
:root{color-scheme:light dark;
--bg:#ffffff;--surface:#f7f9fc;--surface2:#eef2f9;--ink:#0c1424;--muted:#4d5b73;--line:#dde3ec;
--action:#2e63ff;--action-ink:#ffffff;--truth:#07684f;--truth-chip:#14c79b;--chip-ink:#04261d;
--warn-chip:#ffe0a8;--warn-ink:#4a3300;--code:#0b1220;--code-ink:#e9eefb}
@media (prefers-color-scheme:dark){:root{
--bg:#080d18;--surface:#0f1729;--surface2:#152037;--ink:#e9eefc;--muted:#a4b2ca;--line:#22304c;
--action:#8aa6ff;--action-ink:#06122e;--truth:#14c79b;--truth-chip:#14c79b;--chip-ink:#04261d;
--warn-chip:#5a4310;--warn-ink:#ffe0a8;--code:#050a14;--code-ink:#dfe7fb}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main,header.top,footer{max-width:60rem;margin:0 auto;padding:0 1.25rem}
header.top{display:flex;flex-wrap:wrap;gap:1rem;align-items:baseline;justify-content:space-between;
padding-top:2rem;padding-bottom:.5rem}
header.top .brand{font-weight:700;letter-spacing:-.01em}
header.top nav a{margin-left:1rem}
a{color:var(--action)}
a:hover{text-decoration-thickness:2px}
h1{font-size:clamp(1.9rem,4.5vw,2.7rem);line-height:1.15;letter-spacing:-.02em;margin:.6rem 0}
h2{font-size:1.5rem;letter-spacing:-.01em;margin-top:3rem;border-top:1px solid var(--line);padding-top:1.5rem}
h3{font-size:1.2rem;margin:0}
h4{font-size:.95rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:1.4rem 0 .4rem}
p{margin:.7rem 0}
.lead{font-size:1.2rem}
.hero-truth{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--truth-chip);
border-radius:10px;padding:1rem 1.1rem}
.facts-strip{display:flex;flex-wrap:wrap;gap:.5rem;margin:1.2rem 0}
.chip{display:inline-block;padding:.2rem .6rem;border-radius:999px;font-size:.85rem;font-weight:600;
border:1px solid var(--line);background:var(--surface2);color:var(--ink)}
.chip-price{background:var(--action);color:var(--action-ink);border-color:transparent}
.chip-ok{background:var(--truth-chip);color:var(--chip-ink);border-color:transparent}
.chip-off{background:var(--warn-chip);color:var(--warn-ink);border-color:transparent}
.chip-fact{background:var(--surface2)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:1.25rem;margin:1.25rem 0}
.card-head{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;justify-content:space-between}
.chips{display:flex;flex-wrap:wrap;gap:.4rem}
.endpoints{list-style:none;padding:0;margin:.3rem 0}
.endpoints li{margin:.2rem 0}
code{background:var(--surface2);padding:.1rem .35rem;border-radius:5px;font-size:.9em;
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre{background:var(--code);color:var(--code-ink);padding:.9rem 1rem;border-radius:10px;overflow-x:auto;
font-size:.85rem;line-height:1.5}
pre code{background:none;color:inherit;padding:0}
.note{color:var(--muted);font-size:.92rem}
.disclosure{background:var(--surface2);border-radius:10px;padding:.9rem 1rem;margin:1rem 0}
.disclosure h4{margin-top:0}
table.facts{border-collapse:collapse;width:100%;font-size:.9rem}
table.facts th{text-align:left;font-weight:600;color:var(--muted);padding:.15rem .8rem .15rem 0;white-space:nowrap}
table.facts td{padding:.15rem 0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
details{margin:1rem 0}
summary{cursor:pointer;font-weight:600}
dl.faq dt{font-weight:600;margin-top:1.1rem}
dl.faq dd{margin:.3rem 0 0;color:var(--muted)}
footer{border-top:1px solid var(--line);margin-top:3rem;padding-top:1.25rem;padding-bottom:3rem;
color:var(--muted);font-size:.9rem}
.group-blurb{color:var(--muted)}
`;

/**
 * Render the landing page.
 *
 * @param {object} opts.cfg      gateway config (URLs, network, asset)
 * @param {object} opts.catalog  the SAME object /x402 returns as JSON
 * @param {Array}  opts.products registry product objects (for the live 402 example)
 */
function renderLanding({ cfg, catalog, products = [], now = Date.now }) {
  const l = links(cfg);
  const facts = networkFacts(cfg);
  const id = identity(cfg);
  const sellable = catalog.products.filter((p) => !p.development_only);
  const byId = new Map(sellable.map((p) => [p.id, p]));
  const ordered = [
    ...ORDER.map((pid) => byId.get(pid)).filter(Boolean),
    ...sellable.filter((p) => !ORDER.includes(p.id)),
  ];
  const qrng = byId.get('qrng');
  const entropy = qrng && qrng.entropy;
  const challenge = exampleChallenge(products, cfg);
  const faq = faqFor(catalog, facts);

  const description =
    `Pay-per-request APIs for autonomous agents on Animica: verifiable randomness with a signed attestation${qrng ? ` from $${qrng.price}` : ''}, bulk post-quantum L1 chain data and capacity-gated AI inference. Paid with ${facts.asset || 'a stablecoin'} on ${networkDisplayName(facts)} over the open x402 protocol — no account, no API key, no subscription.`;

  const webApi = {
    '@context': 'https://schema.org',
    '@type': 'WebAPI',
    name: id.name,
    description,
    url: l.landing,
    documentation: l.landing,
    termsOfService: l.landing,
    provider: {
      '@type': 'Organization',
      name: id.provider,
      url: id.homepage,
      email: CONTACT,
    },
    potentialAction: {
      '@type': 'ConsumeAction',
      target: { '@type': 'EntryPoint', urlTemplate: `${l.base}/x402/{product}`, httpMethod: ['GET', 'POST'] },
    },
    offers: sellable.map((p) => ({
      '@type': 'Offer',
      name: p.name,
      url: p.url,
      availability: p.available ? 'https://schema.org/InStock' : 'https://schema.org/OutOfStock',
      priceSpecification: {
        '@type': 'UnitPriceSpecification',
        price: p.price,
        priceCurrency: 'USD',
        unitText: 'request',
        description: `paid as ${p.price_atomic} atomic units of ${facts.asset || 'the configured asset'} on ${networkDisplayName(facts)} (${facts.network_caip2}) via x402`,
      },
    })),
  };
  const faqPage = {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: faq.map((f) => ({
      '@type': 'Question',
      name: f.q,
      acceptedAnswer: { '@type': 'Answer', text: f.a },
    })),
  };

  const groupsHtml = GROUPS.map((g) => {
    const members = g.ids.map((pid) => byId.get(pid)).filter(Boolean);
    if (!members.length) return '';
    const verifier = g.verifier
      ? `<p class="group-blurb">Verifier: <a href="${esc(VERIFIER_URL)}"><code>randomness/beacon_api/static/verify.js</code></a> — zero-dependency, runs in Node and the browser, and is the same file every response names in its <code>verification</code> block.</p>`
      : '';
    return `<h2 id="${esc(g.id)}">${esc(g.title)}</h2>
<p class="group-blurb">${esc(g.blurb)}</p>
${verifier}
${members.map((entry) => productCard(entry, facts, l.base)).join('\n')}`;
  }).join('\n');
  const leftovers = ordered.filter((p) => !GROUPS.some((g) => g.ids.includes(p.id)));
  const leftoversHtml = leftovers.length
    ? `<h2 id="more">More products</h2>\n${leftovers.map((entry) => productCard(entry, facts, l.base)).join('\n')}`
    : '';

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Animica x402 — pay-per-request APIs for agents</title>
<meta name="description" content="${esc(description)}">
<meta name="keywords" content="x402 API, pay-per-request API, AI agent payments, USDC API, Base x402, quantum randomness API, QRNG API, blockchain data API, agent API">
<meta name="robots" content="index,follow">
<link rel="canonical" href="${esc(l.landing)}">
<link rel="alternate" type="application/json" href="${esc(l.wellKnown)}" title="x402 machine catalog">
<meta property="og:type" content="website">
<meta property="og:title" content="Animica x402 — pay-per-request APIs for agents">
<meta property="og:description" content="${esc(description)}">
<meta property="og:url" content="${esc(l.landing)}">
<meta name="twitter:card" content="summary">
<style>${CSS}</style>
<script type="application/ld+json">${jsonLd(webApi)}</script>
<script type="application/ld+json">${jsonLd(faqPage)}</script>
</head>
<body>
<header class="top">
  <div class="brand">${esc(id.name)}</div>
  <nav><a href="#randomness">Randomness</a><a href="#chain-data">Chain data</a><a href="#inference">Inference</a><a href="#payment">How payment works</a><a href="#discovery">Discovery</a></nav>
</header>
<main>
  <h1>Pay-per-request APIs for autonomous agents</h1>
  <p class="lead">${qrng ? `Verifiable quantum randomness for $${esc(qrng.price)} per request.` : 'Verifiable randomness, chain data and inference, per request.'} No subscription. No account required. Pay with ${esc(facts.asset || 'a stablecoin')} on ${esc(networkDisplayName(facts))} using x402.</p>
  ${entropy ? `<div class="hero-truth"><p><strong>What "verifiable" means here, stated up front.</strong> Animica's quantum-randomness service is running on its <em>software CSPRNG fallback</em> today: every draw reports <code>source: "${esc(entropy.source)}"</code>, <code>is_quantum: ${esc(String(entropy.is_quantum))}</code> and <code>attested: ${esc(String(entropy.attested))}</code>. <strong>No hardware QRNG is connected and hardware attestation is not live.</strong> What you can check right now, on every response: the node's ed25519 signature over the sha3-256 digest of the exact bytes you received, and — for the derived products — a full recomputation of the result from that draw using the published rules. Those fields come from this gateway's own readiness draw, so this paragraph changes by itself if the hardware ever does.</p></div>` : ''}
  <div class="facts-strip">
    ${chip(`network: ${facts.network || facts.network_caip2}`, 'fact')}
    ${chip(`chain id: ${facts.chain_id}`, 'fact')}
    ${chip(`asset: ${facts.asset || facts.asset_address}`, 'fact')}
    ${chip('protocol: x402 (open spec)', 'fact')}
    ${chip('no account, no API key', 'fact')}
  </div>

  <h2 id="payment">How payment works</h2>
  <p>There is no API key to obtain and no auth header to configure. Every paid route answers <code>402 Payment Required</code> with machine-readable terms; your client signs an authorization for exactly those terms locally and retries. Private keys never reach Animica.</p>
  <ol>
    <li>Request the endpoint normally. You get <code>402</code> with a <code>PAYMENT-REQUIRED</code> header (v2) and the same offer as a JSON body (v1 clients).</li>
    <li>Sign the offered terms locally with any x402 client.</li>
    <li>Retry with the signed authorization in <code>PAYMENT-SIGNATURE</code> (v2) or <code>X-PAYMENT</code> (v1).</li>
    <li>The gateway verifies it, settles it on-chain through its own facilitator, and returns your response with settlement details in <code>PAYMENT-RESPONSE</code>.</li>
  </ol>
  ${challenge
    ? `<p class="note">The live offer for ${esc(qrng ? qrng.name : 'the first product')}, built by the same code that answers a real 402:</p>${json(challenge.accepts[0])}`
    : '<p class="note">This deployment has no payment lane configured yet, so no 402 terms can be shown.</p>'}
  <p class="note">Settlement is done by Animica's own facilitator; no third-party payment service sits between payer and recipient. Amounts are integer atomic units of the asset (6 decimals), never floating point.</p>

${groupsHtml}
${leftoversHtml}

  <h2 id="discovery">Machine discovery</h2>
  <ul>
    <li><a href="${esc(l.wellKnown)}">${esc(l.wellKnown)}</a> — the catalog: products, prices, live availability, input/output schemas.</li>
    <li><a href="${esc(l.catalog)}">${esc(l.catalog)}</a> — the same catalog (this page is what a browser gets; <code>Accept: application/json</code> gets the catalog).</li>
    <li><a href="${esc(l.openapi)}">${esc(l.openapi)}</a> — OpenAPI 3.1, including the 402 challenge flow.</li>
    <li><a href="${esc(l.stats)}">${esc(l.stats)}</a> — aggregate settlement counts (no payer addresses).</li>
    <li><a href="${esc(REPO)}">${esc(REPO)}</a> — source, including the verifier used in the randomness examples.</li>
  </ul>
  <p class="note">Availability in the catalog is live: a product whose backend is unhealthy reports <code>available: false</code> with a reason, and its endpoint answers 503 <em>without</em> asking for payment. Nothing here is ever sold while it is known to be unavailable.</p>

  <h2 id="faq">FAQ</h2>
  <dl class="faq">
${faq.map((f) => `    <dt>${esc(f.q)}</dt>\n    <dd>${esc(f.a)}</dd>`).join('\n')}
  </dl>
</main>
<footer>
  <p>${esc(id.provider)} · <a href="${esc(HOMEPAGE)}">${esc(HOMEPAGE)}</a> · <a href="mailto:${esc(CONTACT)}">${esc(CONTACT)}</a> · ${esc(id.license)}</p>
  <p>Prices, availability and the entropy disclosure on this page were rendered from the live product registry at ${esc(new Date(now()).toISOString())}. Response examples were captured from real calls on ${esc(CAPTURED_AT)}.</p>
</footer>
</body>
</html>
`;
}

module.exports = { renderLanding, ORDER, GROUPS, VERIFIABILITY, esc };
