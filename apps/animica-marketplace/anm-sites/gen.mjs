#!/usr/bin/env node
/**
 * Generate the native .anm sites from a compact spec, all sharing animica.anm's design system.
 * Each output is a full, self-contained HTML document (the gateway serves it as raw, hash-verified
 * bytes on an opaque-origin sandbox). Run:  node anm-sites/gen.mjs   then publish with publish_anm_sites.mjs.
 *
 * animica.html (the flagship search homepage) is hand-authored and NOT regenerated here.
 */
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const GW = 'https://animica.dev';

const STYLE = `
:root{
  --ink:#070b16;--surface:#0b1224;--card:#111c38;--line:#1b2748;--line2:#26355f;
  --text:#e8ecf6;--muted:#93a1c0;--muted2:#66739a;
  --iris:#6d5ef6;--cyan:#37e0d8;--amber:#ffb020;--green:#34e5a0;--r:14px;
}
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{background:
  radial-gradient(1100px 620px at 50% -8%,rgba(109,94,246,.20),transparent 62%),
  radial-gradient(760px 460px at 88% 8%,rgba(55,224,216,.10),transparent 60%),var(--ink);
  color:var(--text);font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
  -webkit-font-smoothing:antialiased;min-height:100vh}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
a{color:inherit;text-decoration:none}
.wrap{max-width:940px;margin:0 auto;padding:0 18px}
header{display:flex;align-items:center;gap:10px;padding:16px 0 4px}
.mark{width:26px;height:26px;border-radius:8px;background:linear-gradient(135deg,var(--iris),var(--cyan));
  display:grid;place-items:center;font-weight:800;color:#050914;font-size:15px;flex:0 0 auto}
.brand{font-weight:650;letter-spacing:-.01em}
header .sp{flex:1}
.chip{font-size:11.5px;color:var(--muted);border:1px solid var(--line2);border-radius:999px;padding:4px 10px;white-space:nowrap}
.live{display:inline-flex;align-items:center;gap:6px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 0 3px rgba(52,229,160,.15)}
.hero{text-align:center;padding:min(8vh,60px) 0 6px}
.emoji{font-size:40px;line-height:1;margin-bottom:8px}
h1{font-size:clamp(28px,7vw,46px);line-height:1.04;letter-spacing:-.035em;margin:0 0 10px;text-wrap:balance}
h1 .g{background:linear-gradient(110deg,var(--cyan),var(--iris));-webkit-background-clip:text;background-clip:text;color:transparent}
.lede{color:var(--muted);font-size:clamp(14px,3.4vw,17px);margin:0 auto;max-width:58ch}
.cta{display:inline-flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:18px}
.btn{border:0;border-radius:999px;padding:11px 18px;font-weight:650;font-size:14px;cursor:pointer;color:#04122a;
  background:linear-gradient(180deg,var(--cyan),#2bb6cf);display:inline-block}
.btn.alt{background:transparent;color:var(--text);border:1px solid var(--line2)}
.btn:active{transform:translateY(1px)}
section{margin:34px 0}
h2{font-size:13px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted2);margin:0 0 12px;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:14px;display:block;transition:border-color .15s,transform .15s}
.card:hover{border-color:var(--iris);transform:translateY(-2px)}
.card .t{display:flex;align-items:center;gap:9px;margin-bottom:7px}
.card .n{font-size:14.5px;color:var(--cyan)}
.card p{margin:0;color:var(--muted);font-size:12.5px;line-height:1.5}
.pill{font-size:10.5px;color:var(--muted);border:1px solid var(--line2);border-radius:999px;padding:1px 7px;flex:0 0 auto}
.native{font-size:10.5px;color:var(--green);border:1px solid rgba(52,229,160,.35);background:rgba(52,229,160,.08);border-radius:999px;padding:1px 7px}
.empty{color:var(--muted2);text-align:center;padding:18px;font-size:13.5px}
.steps{counter-reset:s;display:grid;gap:10px}
.step{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:13px 14px 13px 46px;position:relative}
.step::before{counter-increment:s;content:counter(s);position:absolute;left:12px;top:12px;width:24px;height:24px;border-radius:7px;
  background:linear-gradient(135deg,var(--iris),var(--cyan));color:#050914;font-weight:800;font-size:13px;display:grid;place-items:center}
.step b{font-size:14px}.step p{margin:4px 0 0;color:var(--muted);font-size:12.5px}
pre{background:#060b18;border:1px solid var(--line);border-radius:10px;padding:12px;overflow-x:auto;margin:8px 0 0;
  font:12.5px/1.55 ui-monospace,Menlo,Consolas,monospace;color:#cdd6ee}
pre .c{color:var(--green)}
footer{border-top:1px solid var(--line);margin-top:36px;padding:18px 0 30px;color:var(--muted2);font-size:12.5px;display:flex;gap:14px;flex-wrap:wrap;align-items:center}
footer .sp{flex:1}footer a{color:var(--muted)}footer a:hover{color:var(--cyan)}
@media (max-width:520px){.hero{padding-top:24px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
`;

// widget scripts (self-contained, opaque-origin safe: pin the gateway, no referrer needed)
const WIDGETS = {
  // live grid of .anm names, optionally filtered by kind
  directory: (kind) => `
  fetch(API+'/names?search=${kind ? '&kind=' + kind : ''}')
    .then(r=>r.json()).then(d=>{
      var rows=(d.results||[])${kind ? `.filter(x=>x.kind==='${kind}')` : ''}.sort((a,b)=>a.name.localeCompare(b.name));
      G('grid').innerHTML = rows.length? rows.map(function(r){
        return '<a class="card" href="'+U(r.name)+'" target="_blank" rel="noopener"><div class="t"><span>'+av(r)+'</span>'
          +'<span class="n mono">'+E(r.name)+'.anm</span>'+(r.contentCid?'<span class="native">native</span>':'')+'</div>'
          +'<p>'+E(desc(r))+'</p></a>';
      }).join('') : '<div class="empty">Nothing here yet.</div>';
      if(typeof d.total==='number'){var s=G('stat'); if(s) s.textContent=d.total+' sites live';}
    }).catch(()=>{G('grid').innerHTML='<div class="empty">Could not reach the index.</div>';});`,
  // marketplace listings grid, optional type filter
  listings: (type) => `
  fetch(API+'/listings'+(${type ? `'?type=${type}'` : `''`}))
    .then(r=>r.json()).then(d=>{
      var rows=(d.listings||d.results||d.items||[]);
      G('grid').innerHTML = rows.length? rows.slice(0,12).map(function(l){
        var slug=l.slug||l.id; var kind=(l.type||l.kind||'').toString().toLowerCase();
        return '<a class="card" href="'+GW+'/marketplace/'+encodeURIComponent(slug)+'" target="_blank" rel="noopener">'
          +'<div class="t"><span>'+(l.avatar||'\\u2728')+'</span><span class="n">'+E(l.title||l.name||slug)+'</span>'
          +(kind?'<span class="pill">'+E(kind)+'</span>':'')+'</div><p>'+E(l.summary||l.description||'')+'</p></a>';
      }).join('') : '<div class="empty">No listings yet \\u2014 <a style="color:var(--cyan)" href="'+GW+'/marketplace" target="_blank" rel="noopener">open the marketplace</a>.</div>';
    }).catch(()=>{G('grid').innerHTML='<div class="empty">Could not reach the marketplace.</div>';});`,
  // dVPN exit locations
  exits: () => `
  fetch(API+'/vpn/locations').then(r=>r.json()).then(d=>{
    var rows=(d.exits||[]);
    G('grid').innerHTML = rows.length? rows.slice(0,12).map(function(e){
      var loc=[e.city,e.country||e.region].filter(Boolean).join(', ')||e.label||e.id;
      return '<div class="card"><div class="t"><span>\\uD83D\\uDEE1\\uFE0F</span><span class="n">'+E(loc)+'</span>'
        +(e.proxyAuth?'<span class="pill">token</span>':'<span class="pill">open</span>')+'</div>'
        +'<p>load '+Math.round((e.load||0)*100)+'% \\u00b7 rep '+((e.reputation||0).toFixed(2))+'</p></div>';
    }).join('') : '<div class="empty">No exits online right now.</div>';
    var s=G('stat'); if(s) s.textContent=(rows.length||0)+' exits online';
  }).catch(()=>{G('grid').innerHTML='<div class="empty">Could not reach the exit registry.</div>';});`,
};

function page(spec) {
  const links = (spec.links || []).map((l) =>
    `<a class="card" href="${l.href}" target="_blank" rel="noopener"><div class="t"><span>${l.icon}</span><span class="n">${l.title}</span></div><p>${l.body}</p></a>`
  ).join('');
  const steps = (spec.steps || []).map((s) =>
    `<div class="step"><b>${s.t}</b><p>${s.p}</p>${s.code ? `<pre>${s.code}</pre>` : ''}</div>`
  ).join('');
  const widget = spec.widget ? WIDGETS[spec.widget.kind](spec.widget.arg) : '';

  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>${spec.name}.anm — ${spec.tagline}</title>
<meta name="description" content="${spec.desc}" />
<style>${STYLE}</style>
</head><body>
<div class="wrap">
  <header>
    <a class="mark" href="${GW}/anm/animica" title="animica.anm">A</a>
    <div class="brand">${spec.name}<span style="color:var(--amber)">.anm</span></div>
    <div class="sp"></div>
    <span class="chip live"><span class="dot"></span><span id="stat">${spec.chip || 'on the Animica Internet'}</span></span>
  </header>

  <div class="hero">
    <div class="emoji">${spec.emoji}</div>
    <h1>${spec.h1}</h1>
    <p class="lede">${spec.lede}</p>
    <div class="cta">
      <a class="btn" href="${spec.primary.href}" target="_blank" rel="noopener">${spec.primary.label}</a>
      ${spec.secondary ? `<a class="btn alt" href="${spec.secondary.href}" target="_blank" rel="noopener">${spec.secondary.label}</a>` : ''}
    </div>
  </div>

  ${spec.widget ? `<section><h2>${spec.widget.heading}</h2><div class="grid" id="grid"><div class="empty">Loading…</div></div></section>` : ''}
  ${steps ? `<section><h2>${spec.stepsHeading || 'How it works'}</h2><div class="steps">${steps}</div></section>` : ''}
  ${links ? `<section><h2>${spec.linksHeading || 'Explore'}</h2><div class="grid">${links}</div></section>` : ''}

  <footer>
    <span>${spec.name}.anm — a native site on the Animica Internet, served from its content ID.</span>
    <span class="sp"></span>
    <a href="${GW}/anm/animica" target="_blank" rel="noopener">Home</a>
    <a href="${GW}/docs" target="_blank" rel="noopener">Docs</a>
    <a href="${GW}/portal" target="_blank" rel="noopener">Portal</a>
  </footer>
</div>
${spec.widget ? `<script>(function(){
  var GW='${GW}', API=GW+'/api/mkt/v1';
  function G(i){return document.getElementById(i);}
  function E(s){var d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML;}
  function rec(r){try{return JSON.parse(r.recordsJson)||{};}catch(e){return {};}}
  function desc(r){var x=rec(r);return x.description||(r.kind?('A '+r.kind+' on the Animica Internet.'):'');}
  function av(r){var x=rec(r);return x.avatar||(r.kind==='agent'?'\\uD83E\\uDD16':(r.kind==='ai'?'\\u2728':'\\uD83C\\uDF10'));}
  function U(n){return GW+'/anm/'+encodeURIComponent(n);}
  ${widget}
})();</script>` : ''}
</body></html>`;
}

const SITES = [
  {
    name: 'animal', emoji: '🐾', tagline: 'the Animica mascot',
    desc: 'Meet the Animica Animal — an autonomous AI mascot that grows the network by making content, grounded on real metrics and posted only to accounts its operator connects.',
    h1: 'The network has a <span class="g">mascot</span>',
    lede: 'The Animica Animal is an autonomous AI creature that watches what is happening on the network and turns it into content — media rendered by miners, captions grounded on real metrics, published only to accounts its operator owns.',
    primary: { href: `${GW}/animal`, label: 'Open the console' },
    secondary: { href: `${GW}/anm/agents`, label: 'Meet the agents' },
    chip: 'autonomous mascot',
    steps: [
      { t: 'Give it a goal', p: 'From the operator console you set what the Animal should grow toward and chat to steer it. It is off by default — it never posts until you connect an account and switch it live.' },
      { t: 'It makes real content', p: 'Captions are grounded on live network metrics, and media — vertical video with custom music for TikTok, images and clips — is generated by GPU miners through the same job queue everything else uses. No AI runs on the gateway.' },
      { t: 'Only your accounts, double-gated', p: 'It posts solely to social accounts you explicitly connect and own; tokens are sealed and never exposed to the browser. Going live needs two independent switches — dry-run is the default. No self-signup, scraping or CAPTCHA-bypass.' },
    ],
    stepsHeading: 'How the Animal works',
    links: [
      { icon: '🎬', title: 'media.anm', href: `${GW}/anm/media`, body: 'The generative media the Animal draws on — image, video and audio, mined on demand.' },
      { icon: '🤖', title: 'agents.anm', href: `${GW}/anm/agents`, body: 'Other autonomous agents that hold their own .anm names and wallets.' },
      { icon: '🎛️', title: 'Operator console', href: `${GW}/animal`, body: 'Sign in, connect accounts, set a goal and watch what it posts.' },
    ],
  },
  {
    name: 'market', emoji: '🛒', tagline: 'the ANM-native AI marketplace',
    desc: 'Buy and sell AI agents, RAG assistants, knowledge bases and generative media — settled in ANM.',
    h1: 'The <span class="g">AI marketplace</span>, native to ANM',
    lede: 'Agents, RAG assistants, knowledge AIs and generative media — listed by their creators, paid in ANM, served by the network.',
    primary: { href: `${GW}/marketplace`, label: 'Open the marketplace' },
    secondary: { href: `${GW}/anm/agents`, label: 'Browse agents' },
    chip: 'live listings',
    widget: { kind: 'listings', heading: 'Featured on the marketplace' },
    links: [
      { icon: '🤖', title: 'agents.anm', href: `${GW}/anm/agents`, body: 'Autonomous agents with their own .anm identities and wallets.' },
      { icon: '🎬', title: 'media.anm', href: `${GW}/anm/media`, body: 'Image, video and audio generated by miners on demand.' },
      { icon: '📚', title: 'Sell your own', href: `${GW}/marketplace`, body: 'List an agent, a RAG assistant or a knowledge AI in minutes.' },
    ],
  },
  {
    name: 'agents', emoji: '🤖', tagline: 'autonomous agents on the Animica Internet',
    desc: 'A directory of autonomous agents that hold their own .anm names, wallets and API keys.',
    h1: 'Agents that <span class="g">own their identity</span>',
    lede: 'Every agent here holds an ANM wallet and a .anm name, so it can be paid, resolved and reasoned about like any other citizen of the network.',
    primary: { href: `${GW}/names?kind=agent`, label: 'Browse all agents' },
    secondary: { href: `${GW}/docs#agents`, label: 'Build an agent' },
    chip: 'agent directory',
    widget: { kind: 'directory', arg: 'agent', heading: 'Live agents' },
    links: [
      { icon: '🛒', title: 'market.anm', href: `${GW}/anm/market`, body: 'Hire agents and AI services, paid in ANM.' },
      { icon: '🧠', title: 'Give an agent a name', href: `${GW}/names`, body: 'Register agent.anm and link it to an agent profile + wallet.' },
      { icon: '📖', title: 'Agent API', href: `${GW}/docs#agents`, body: 'The messaging + task API agents use to act on-chain.' },
    ],
  },
  {
    name: 'media', emoji: '🎬', tagline: 'generative media served by miners',
    desc: 'Generate images, video and audio on the Animica network — rendered by miners, paid in ANM.',
    h1: 'Generative <span class="g">media</span>, mined',
    lede: 'Image, video and audio generation is a job type on the network: miners render it, you pay in ANM, and the result is content-addressed.',
    primary: { href: `${GW}/marketplace?type=MEDIA`, label: 'Generate media' },
    secondary: { href: `${GW}/docs#media`, label: 'Media API' },
    chip: 'image · video · audio',
    widget: { kind: 'listings', arg: 'MEDIA', heading: 'Media services' },
    links: [
      { icon: '🖼️', title: 'Image generation', href: `${GW}/marketplace?type=MEDIA`, body: 'Text-to-image rendered by GPU miners via AICF.' },
      { icon: '🎞️', title: 'Video workflows', href: `${GW}/marketplace?type=MEDIA`, body: 'Multi-step video generation as a mined job.' },
      { icon: '🔊', title: 'Audio', href: `${GW}/marketplace?type=MEDIA`, body: 'Speech and music generation on the network.' },
    ],
  },
  {
    name: 'vpn', emoji: '🛡️', tagline: 'the decentralized VPN',
    desc: 'Pick an exit anywhere and route your traffic through it; relays earn ANM for the bandwidth they carry.',
    h1: 'Browse from <span class="g">anywhere</span>',
    lede: 'A real WireGuard tunnel to an exit you choose — or a browser-only proxy. Single-hop: it hides your traffic from your ISP and LAN, not from the exit operator. Relays earn ANM.',
    primary: { href: `${GW}/vpn`, label: 'Pick an exit' },
    secondary: { href: `${GW}/browser`, label: 'Get the extension' },
    chip: 'exits online',
    widget: { kind: 'exits', heading: 'Exit locations' },
    steps: [
      { t: 'Install the client', p: 'The native client is the only system-wide tunnel; the browser extension is a proxy for just your browser.', code: 'pip install -U animica\n<span class="c"># then</span>\nanimica vpn up --region eu' },
      { t: 'It is fail-closed', p: 'A killswitch drops all egress if the tunnel dies, DNS is forced through the tunnel, and `animica vpn doctor` proves your IP actually changed before it calls you protected.' },
      { t: 'Run an exit, earn ANM', p: 'Opt in to relay traffic and accrue bandwidth rewards (IOUs, treasury-settled). Off by default and isolated from any node.', code: 'animica vpn exit --region us --i-am-not-the-validator' },
    ],
    stepsHeading: 'Get connected',
    links: [
      { icon: '🧩', title: 'Browser extension', href: `${GW}/browser`, body: 'A location picker that proxies just your browser.' },
      { icon: '📖', title: 'dVPN docs', href: `${GW}/docs#vpn`, body: 'CLI reference, killswitch, running an exit safely.' },
    ],
  },
  {
    name: 'wallet', emoji: '👛', tagline: 'the non-custodial post-quantum wallet',
    desc: 'Hold ANM and sign with post-quantum ML-DSA keys — keys never leave your device.',
    h1: 'Your keys, <span class="g">quantum-safe</span>',
    lede: 'A non-custodial ANM wallet built on ML-DSA-65 (FIPS-204) signatures. Keys are generated and stored in your browser; only signed transactions ever leave.',
    primary: { href: 'https://wallet.animica.org', label: 'Open the wallet' },
    secondary: { href: `${GW}/browser`, label: 'Get the extension' },
    chip: 'ML-DSA-65 · non-custodial',
    steps: [
      { t: 'Create a vault', p: 'A post-quantum keypair is generated in your browser and sealed in an AES-GCM vault behind your password.' },
      { t: 'Get a name', p: 'Register yourname.anm and link it to your wallet so people (and agents) can pay you by name.' },
      { t: 'Sign, don\'t surrender', p: 'The wallet only ever emits signed transactions — it never uploads your secret key.' },
    ],
    stepsHeading: 'Get started',
    links: [
      { icon: '🌐', title: 'Register a name', href: `${GW}/names`, body: 'Claim yourname.anm and point it at your wallet.' },
      { icon: '🛡️', title: 'Browse privately', href: `${GW}/anm/vpn`, body: 'Route your wallet traffic through a dVPN exit.' },
    ],
  },
  {
    name: 'forge', emoji: '🔨', tagline: 'prompt to app',
    desc: 'Describe an app and Animica Forge builds it — powered by the Animica AI network.',
    h1: 'Describe it. <span class="g">Forge</span> builds it.',
    lede: 'Prompt-to-app on the Animica network: a builder, a gallery and an arena, with a window.animica wallet baked in.',
    primary: { href: 'https://animica.io', label: 'Open Forge' },
    secondary: { href: `${GW}/anm/studio`, label: 'Try Studio' },
    chip: 'prompt → app',
    links: [
      { icon: '🎛️', title: 'studio.anm', href: `${GW}/anm/studio`, body: 'An agentic coding studio powered by Animica AI.' },
      { icon: '🛒', title: 'market.anm', href: `${GW}/anm/market`, body: 'Ship what you build to the AI marketplace.' },
    ],
  },
  {
    name: 'studio', emoji: '🎛️', tagline: 'build with Animica AI',
    desc: 'An agentic coding studio powered by the Animica /v1 API.',
    h1: 'Code with <span class="g">Animica AI</span>',
    lede: 'Animica Studio is an agentic coding surface backed by the network\'s free, OpenAI-compatible /v1 API — no key required to start.',
    primary: { href: `${GW}/studio`, label: 'Open Studio' },
    secondary: { href: `${GW}/docs#api`, label: 'The /v1 API' },
    chip: 'agentic coding',
    links: [
      { icon: '🔨', title: 'forge.anm', href: `${GW}/anm/forge`, body: 'Prompt-to-app, if you\'d rather describe than code.' },
      { icon: '📖', title: 'Free /v1 API', href: `${GW}/docs#api`, body: 'OpenAI-compatible chat, embeddings and media — keyless to start.' },
    ],
  },
  {
    name: 'search', emoji: '🔎', tagline: 'search the sovereign namespace',
    desc: 'Search every name on the Animica Internet.',
    h1: 'Search the <span class="g">.anm</span> web',
    lede: 'Every .anm name is resolvable by any node — this is the full, live index. Owned by wallets, not a registrar.',
    primary: { href: `${GW}/anm/animica`, label: 'Go to animica.anm' },
    secondary: { href: `${GW}/names`, label: 'Full directory' },
    chip: 'live index',
    widget: { kind: 'directory', heading: 'Every site on the Animica Internet' },
    links: [
      { icon: '🌐', title: 'Register a name', href: `${GW}/names`, body: 'Claim any free name with an ANM wallet.' },
      { icon: '🚀', title: 'Deploy a native site', href: `${GW}/docs#deploy`, body: 'Publish self-contained HTML the network serves for you.' },
    ],
  },
  {
    name: 'docs', emoji: '📚', tagline: 'developer docs & Internet tutorials',
    desc: 'Complete tutorials for using the Animica Internet, deploying your own .anm site, the dVPN, and every developer API.',
    h1: 'Build on the <span class="g">Animica Internet</span>',
    lede: 'Tutorials for using the .anm web and deploying your own site, plus the full developer reference: the free /v1 AI API, the marketplace + ANS APIs, the CLI, and the post-quantum wallet.',
    primary: { href: `${GW}/docs`, label: 'Open the full docs' },
    secondary: { href: `${GW}/docs#deploy`, label: 'Deploy a .anm site' },
    chip: 'tutorials + reference',
    steps: [
      { t: 'Register a name', p: 'Claim a name with an ANM wallet — no registrar. It is yours until it expires.', code: 'curl -X POST '+GW+'/api/mkt/v1/names \\\n  -H "authorization: Bearer $ANM_KEY" \\\n  -d \'{"name":"you","years":1}\'' },
      { t: 'Publish native HTML', p: 'Upload a self-contained page; the network stores it by content ID and serves it hash-verified.', code: 'curl -X POST '+GW+'/api/mkt/v1/names/you/publish \\\n  -H "authorization: Bearer $ANM_KEY" \\\n  --data-binary @index.html' },
      { t: 'Visit it', p: 'Anyone can open you.anm through the gateway, or in the Animica browser.', code: GW+'/anm/you' },
    ],
    stepsHeading: 'Deploy your own .anm site in 3 steps',
    links: [
      { icon: '⚡', title: 'Free /v1 AI API', href: `${GW}/docs#api`, body: 'OpenAI-compatible chat, embeddings, images — keyless to start.' },
      { icon: '🌐', title: 'ANS / names API', href: `${GW}/docs#ans`, body: 'Register, publish, transfer and resolve .anm names.' },
      { icon: '🛡️', title: 'dVPN', href: `${GW}/docs#vpn`, body: 'Connect, verify no leaks, and run an exit for ANM.' },
    ],
  },
];

let n = 0;
for (const spec of SITES) {
  writeFileSync(join(HERE, `${spec.name}.html`), page(spec));
  n += 1;
  console.log(`  wrote ${spec.name}.html  (${page(spec).length}B)`);
}
console.log(`\ngenerated ${n} native .anm site(s). Publish with: node scripts/publish_anm_sites.mjs`);
