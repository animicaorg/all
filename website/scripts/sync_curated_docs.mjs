#!/usr/bin/env node
/**
 * sync_curated_docs.mjs — build the /docs reference section from curated repository docs.
 *
 * Reads a fixed manifest of files under <repo>/docs, processes each one and writes
 * src/content/docs/<group>/<slug>.md with frontmatter for the `docs` content collection.
 *
 * Processing (idempotent — the whole src/content/docs tree is regenerated each run):
 *   - first H1 becomes the frontmatter title; any further H1s are demoted to H2
 *   - a "Source:" note is prepended; status notes are added when a doc uses legacy
 *     PQ naming (Dilithium3 / SPHINCS+) or devnet/faucet examples
 *   - relative links between curated docs → /docs/<group>/<slug>[#hash]
 *   - relative links to other repo files → https://github.com/animicaorg/all/blob|tree/main/<path>
 *   - relative links to files that do not exist → unlinked (text kept)
 *   - images: copied to public/docs-assets/ if they exist, dropped otherwise
 *   - angle-bracket placeholders outside code (e.g. <addr>) are HTML-escaped
 *   - tab-indented "•" bullet lists are converted to markdown lists
 *   - every output must be ≥ 150 words
 *
 * Usage:  node scripts/sync_curated_docs.mjs [--repo /path/to/animica] [--dry-run]
 */

import { promises as fs } from "node:fs";
import fssync from "node:fs";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const SITE_ROOT = path.resolve(__dirname, "..");

const args = process.argv.slice(2);
const argVal = (flag, dflt) => {
  const i = args.indexOf(flag);
  return i >= 0 && args[i + 1] ? args[i + 1] : dflt;
};
const REPO = path.resolve(argVal("--repo", path.resolve(SITE_ROOT, "..")));
const DRY = args.includes("--dry-run");
const OUT_DIR = path.join(SITE_ROOT, "src", "content", "docs");
const ASSET_DIR = path.join(SITE_ROOT, "public", "docs-assets");
const GITHUB = "https://github.com/animicaorg/all";
const MIN_WORDS = 150;

/* ------------------------------------------------------------------ groups */

export const GROUPS = [
  { key: "getting-started", title: "Getting started" },
  { key: "protocol", title: "Protocol" },
  { key: "contracts", title: "Smart contracts (Python VM)" },
  { key: "tutorials", title: "Tutorials" },
  { key: "l2", title: "Layer 2 rollup" },
  { key: "aicf", title: "AICF and compute" },
  { key: "wallets", title: "Wallets" },
  { key: "mining", title: "Mining" },
  { key: "compat", title: "Compatibility and payments" },
];

/* ---------------------------------------------------------------- manifest */
// src is relative to the repo root. order is the position inside the group.

const MANIFEST = [
  // Getting started
  { src: "docs/ANIMICA_2026_STATE.md", group: "getting-started", slug: "current-state", order: 1,
    description: "The authoritative 2026 overview: ML-DSA-65 as the only accepted signature scheme, the height-gated consensus forks, the free AI layer at animica.dev, and how mining and AI serving fit together. When older documents disagree with this page, this page is correct." },
  { src: "docs/cli-commands.md", group: "getting-started", slug: "cli-commands", order: 2,
    description: "Reference for the command-line tools shipped in the repository: network profiles, the node, wallet, pool and miner CLIs, with their key flags and example invocations." },
  { src: "docs/TX_WORKFLOW.md", group: "getting-started", slug: "transaction-workflow", order: 3,
    description: "Step by step on a local devnet: create a wallet, submit a signed transaction over RPC, watch it in the mempool, see it included in a block, and verify balances." },
  { src: "docs/TROUBLESHOOTING.md", group: "getting-started", slug: "troubleshooting", order: 4,
    description: "Solutions to common node, RPC, wallet, ENA and AICF problems, starting with the doctor commands that diagnose misconfigurations and suggest exact fixes." },

  // Protocol
  { src: "docs/consensus/poies_overview.md", group: "protocol", slug: "poies-overview", order: 1,
    description: "How Proof-of-Integrated-External-Services scores hash work and verified useful-work proofs against the threshold Θ, and the caps and policy that bound each proof type." },
  { src: "docs/DIFFICULTY_ADJUSTMENT.md", group: "protocol", slug: "difficulty-adjustment", order: 2,
    description: "How the Θ (theta) difficulty threshold retargets to hold the target block interval, how retargeting is wired into block import, and how to monitor and configure it." },
  { src: "docs/tx-signing.md", group: "protocol", slug: "transaction-signing", order: 3,
    description: "The canonical signing pipeline: the unsigned canonical-CBOR body, the domain-separated animica.tx.v1 preimage, the SHA3-512 sign-hash, and how nodes verify the signature envelope." },
  { src: "docs/wallet/HD_DERIVATION.md", group: "protocol", slug: "hd-derivation", order: 4,
    description: "The normative HD wallet standard for third-party wallets: BIP-39 mnemonic, SLIP-0010 hardened derivation along m/44'/4279885'/…, the 32-byte ML-DSA-65 seed, and a test vector." },
  { src: "docs/P2P_NETWORKING_GUIDE.md", group: "protocol", slug: "p2p-networking", order: 5,
    description: "How nodes discover peers, complete the post-quantum handshake, gossip blocks and transactions, and sync, with seed lists, ports and configuration for mainnet and development networks." },

  // Smart contracts
  { src: "docs/vm/OVERVIEW.md", group: "contracts", slug: "overview", order: 1,
    description: "Architecture of the deterministic Python VM: the safe Python subset, compilation to IR, the gas-metered interpreter, and how contracts interact with state and host capabilities." },
  { src: "docs/vm/GAS_MODEL.md", group: "contracts", slug: "gas-model", order: 2,
    description: "How execution is metered: opcode costs from the gas table, memory and I/O charges, per-operation caps, and what contract authors should budget for." },
  { src: "docs/vm/SANDBOX.md", group: "contracts", slug: "sandbox", order: 3,
    description: "What the VM forbids to guarantee determinism (I/O, clocks, floats, ambient randomness) and which stdlib modules contracts may import." },
  { src: "docs/vm/CAPABILITIES.md", group: "contracts", slug: "capabilities", order: 4,
    description: "The host syscalls contracts can call for AI jobs, quantum randomness, data availability and the randomness beacon, with their determinism and gas rules." },
  { src: "docs/vm/ABI.md", group: "contracts", slug: "abi", order: 5,
    description: "The canonical byte encoding for function calls, return values and event topics, so SDKs and nodes agree on every contract interaction." },
  { src: "docs/vm/COMPILER.md", group: "contracts", slug: "compiler", order: 6,
    description: "How contract source becomes bytecode: validation, AST-to-IR lowering, type checks, encoding, and static gas estimation." },
  { src: "docs/vm/PATTERNS.md", group: "contracts", slug: "patterns", order: 7,
    description: "Recommended contract patterns for upgrades, proxy pinning, access control and pausability in the deterministic Python VM." },
  { src: "docs/vm/EXAMPLES.md", group: "contracts", slug: "examples", order: 8,
    description: "Annotated example contracts (Counter, Escrow, Token, AI Agent, Quantum RNG) written in the deterministic Python subset with the provided stdlib." },
  { src: "docs/vm/DEBUGGING.md", group: "contracts", slug: "debugging", order: 9,
    description: "Strategies for tracing contract execution: structured logs, receipts, gas reports, and how to reproduce a failing call locally." },

  // Tutorials
  { src: "docs/tutorials/HELLO_COUNTER.md", group: "tutorials", slug: "hello-counter", order: 1,
    description: "Deploy the Counter contract on a local devnet and call inc() and get() from the Python and TypeScript SDKs." },
  { src: "docs/tutorials/TOKEN.md", group: "tutorials", slug: "token", order: 2,
    description: "Implement a minimal Animica-20 (A20) fungible token on the Python VM, with ERC-20-style transfers and signed permits." },
  { src: "docs/tutorials/ESCROW.md", group: "tutorials", slug: "escrow", order: 3,
    description: "Build an escrow contract with disputes and events, then exercise the full deposit, release and dispute flow from the SDK." },
  { src: "docs/tutorials/INDEXER_LITE.md", group: "tutorials", slug: "indexer-lite", order: 4,
    description: "Build a small indexer in one Python process with SQLite that ingests blocks over RPC and plots Γ (gamma) utilization on a static Chart.js page." },
  { src: "docs/tutorials/AI_AGENT.md", group: "tutorials", slug: "ai-agent", order: 5,
    description: "Have a contract enqueue an AI job through the AICF syscall and deterministically consume the result in the next block." },
  { src: "docs/tutorials/DA_ORACLE.md", group: "tutorials", slug: "da-oracle", order: 6,
    description: "Post a blob to the data-availability layer, obtain its NMT commitment, and verify inclusion on-chain from a contract." },
  { src: "docs/tutorials/QUANTUM_RNG.md", group: "tutorials", slug: "quantum-rng", order: 7,
    description: "Consume the chain randomness beacon (commit-reveal and VDF, optionally mixed with QRNG) from a contract safely." },
  { src: "docs/tutorials/PROVIDER_GPU.md", group: "tutorials", slug: "provider-gpu", order: 8,
    description: "Stand up a GPU-backed AICF provider that accepts AI jobs, runs them on NVIDIA hardware, and returns verifiable output digests." },

  // L2
  { src: "docs/l2/ARCHITECTURE.md", group: "l2", slug: "architecture", order: 1,
    description: "The ANM-native Layer 2 payment rollup added in 10.0.0: the components of the l2/ package, sequencing, SMT state, parallel execution, validity by re-execution, and the L1 bridge." },
  { src: "docs/l2/TRANSACTION_LIFECYCLE.md", group: "l2", slug: "transaction-lifecycle", order: 2,
    description: "The explicit state machine every L2 transaction moves through, from submission to L1 settlement, and what each status does and does not guarantee about finality." },
  { src: "docs/l2/FEES.md", group: "l2", slug: "fees", order: 3,
    description: "The deterministic, integer-nanos L2 fee schedule in l2/fees.py and why its constants are consensus-relevant." },
  { src: "docs/l2/DATA_AVAILABILITY.md", group: "l2", slug: "data-availability", order: 4,
    description: "The DA blob the L2 publishes for every batch so that any independent node can rebuild the ledger from published data alone." },
  { src: "docs/l2/FORCED_EXITS.md", group: "l2", slug: "forced-exits", order: 5,
    description: "Forced inclusion and forced exits through the L1 bridge: the mechanism that bounds what a censoring or dead sequencer can do." },
  { src: "docs/l2/SECURITY_ASSUMPTIONS.md", group: "l2", slug: "security-assumptions", order: 6,
    description: "What the 10.0.0 L2 protects, what it assumes, and what remains trusted, written to under-claim rather than over-claim." },
  { src: "docs/l2/PERFORMANCE.md", group: "l2", slug: "performance", order: 7,
    description: "The report template and benchmark harness (animica l2 bench) for L2 throughput claims, with the rule that the real TPS is the slowest pipeline stage." },
  { src: "docs/l2/RUNNING.md", group: "l2", slug: "running", order: 8,
    description: "Running an L2 node: every ANIMICA_L2_* configuration variable, its default, and the operational checks to run." },

  // AICF and compute
  { src: "docs/AICF.md", group: "aicf", slug: "user-guide", order: 1,
    description: "How the AI Compute Fund pool is funded from block rewards and fees, how miners earn and claim credits, and the CLI and RPC calls involved." },
  { src: "docs/aicf/OVERVIEW.md", group: "aicf", slug: "overview", order: 2,
    description: "How AICF coordinates off-chain AI and quantum compute with on-chain accounting: jobs, providers, verification and payouts." },
  { src: "docs/aicf/JOB_API.md", group: "aicf", slug: "job-api", order: 3,
    description: "Contract-facing and operator-facing job schemas, status transitions, receipts and proof formats for the AICF job pipeline." },
  { src: "docs/aicf/CLIENT_GUIDE.md", group: "aicf", slug: "client-guide", order: 4,
    description: "How contracts and end users request AI or quantum compute: the call flow, budgets, result retrieval, SDK usage and common errors." },
  { src: "docs/aicf/PROVIDER_REGISTRY.md", group: "aicf", slug: "provider-registry", order: 5,
    description: "Provider identity, attestation, staking and lifecycle: how AI and quantum providers onboard, stay registered, and deregister." },
  { src: "docs/aicf/SLA.md", group: "aicf", slug: "sla", order: 6,
    description: "Measured latency, quality and redundancy metrics for providers, how they are evaluated, and the penalty schedule." },
  { src: "docs/aicf/SECURITY.md", group: "aicf", slug: "security", order: 7,
    description: "Security guarantees and controls for AICF: TEE attestations for runners, trap-job calibration, and audit procedures." },
  { src: "docs/SECURITY_THREAT_MODEL.md", group: "aicf", slug: "platform-threat-model", order: 8,
    description: "Threat model for the Animica compute and LLM cloud platform: trust boundaries and the threats across authentication, billing, inference, the code sandbox and GitHub integration, with mitigations." },

  // Wallets
  { src: "docs/wallets/RECOVERY.md", group: "wallets", slug: "recovery", order: 1,
    description: "Human-friendly wallet recovery: social recovery for account access and secret splitting for seed custody, and how to combine them." },
  { src: "docs/wallets/SECURITY.md", group: "wallets", slug: "security", order: 2,
    description: "Practical defenses for the extension and Flutter wallets: phishing resistance, avoiding blind signing, and safe transaction simulation." },
  { src: "docs/wallets/HARDWARE.md", group: "wallets", slug: "hardware", order: 3,
    description: "Hardware-backed key flows for Animica accounts and sessions: what Ledger, Trezor, FIDO and WebAuthn can and cannot do for post-quantum signing today." },
  { src: "docs/wallets/ADDRESS_BOOK.md", group: "wallets", slug: "address-book", order: 4,
    description: "The wallet address book: watch-only accounts, light-client verification, and integrity checks that do not require a full node." },

  // Mining
  { src: "docs/tutorials/MINING_GUIDE.md", group: "mining", slug: "local-mining-guide", order: 1,
    description: "A full local mining setup from source: start a devnet with RPC and WebSocket, run the built-in CPU or GPU miner, and expose a Stratum pool for external miners." },
  { src: "docs/MINING_TROUBLESHOOTING.md", group: "mining", slug: "troubleshooting", order: 2,
    description: "Common mining problems and fixes: RPC parameter errors, device selection, rewards not appearing, Θ adjustment, connectivity, and performance." },

  // Compatibility and payments
  { src: "docs/EVM_RPC_COMPAT.md", group: "compat", slug: "evm-rpc-reference", order: 1,
    description: "Repository reference for the Ethereum-compatible RPC facade: eth_*, net_* and web3_* methods, the dedicated chain id 149, the address bridge, and the 9-decimal caveat." },
  { src: "docs/BITCOIN_RPC_COMPAT.md", group: "compat", slug: "bitcoin-rpc-reference", order: 2,
    description: "Repository reference for the Bitcoin-Core-compatible RPC mode: which bitcoin-cli methods are implemented and how their meaning maps onto Animica." },
  { src: "docs/x402.md", group: "compat", slug: "x402", order: 3,
    description: "The complete x402 agent-payments reference for Animica: architecture, threat model, settlement lifecycle, the product catalog, the 402 on the wire, client examples, configuration, deployment and troubleshooting." },
];

/* ------------------------------------------------------------------- notes */

const NOTE_PQ =
  "> **Naming note.** Older repository documents call the signature scheme “Dilithium3”; that is the lineage name for **ML-DSA-65** (FIPS 204, scheme id `0x1003`), the only signature scheme mainnet accepts for new transactions. Where SPHINCS+ is mentioned as a backup scheme, note that on mainnet it is legacy and consensus-stranded: it cannot sign transactions.";
const NOTE_DEVNET =
  "> **Network note.** Examples that mention a local devnet or a faucet apply to development networks only. Mainnet is chain id `1`, reachable at `https://rpc.animica.org/rpc`; there is no mainnet faucet.";

/* --------------------------------------------------------------- utilities */

const HTML_TAGS = new Set([
  "a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "details", "div", "dl", "dt", "em", "h1", "h2", "h3", "h4", "h5", "h6",
  "hr", "i", "img", "kbd", "li", "ol", "p", "pre", "s", "small", "span", "strong", "sub", "summary", "sup", "table", "tbody", "td",
  "th", "thead", "tr", "u", "ul", "var", "mark", "ins", "figure", "figcaption", "caption",
]);

const report = { files: [], escaped: new Map(), unlinked: [], github: 0, internal: 0, imagesDropped: [], imagesCopied: [], demotedH1: 0, bulletsFixed: 0, rawHtmlWarnings: [], repaired: [] };

function words(s) {
  return (s.match(/\S+/g) || []).length;
}

function exists(p) {
  try { fssync.accessSync(p); return true; } catch { return false; }
}

function isDir(p) {
  try { return fssync.statSync(p).isDirectory(); } catch { return false; }
}

/** Split markdown into alternating text / fenced-code segments. */
function splitFences(md) {
  const lines = md.split("\n");
  const segs = [];
  let buf = [];
  let inCode = false;
  let fence = null;
  for (const line of lines) {
    const m = line.match(/^\s*(`{3,}|~{3,})/);
    if (!inCode && m) {
      segs.push({ code: false, text: buf.join("\n") });
      buf = [line];
      inCode = true;
      fence = m[1][0];
      continue;
    }
    if (inCode && m && m[1][0] === fence) {
      buf.push(line);
      segs.push({ code: true, text: buf.join("\n") });
      buf = [];
      inCode = false;
      fence = null;
      continue;
    }
    buf.push(line);
  }
  segs.push({ code: inCode, text: buf.join("\n") });
  return segs;
}

/** Apply fn to the non-inline-code parts of a text segment. */
function mapOutsideInlineCode(text, fn) {
  const re = /(`+)[^`][\s\S]*?\1/g;
  let out = "";
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    out += fn(text.slice(last, m.index)) + m[0];
    last = m.index + m[0].length;
  }
  out += fn(text.slice(last));
  return out;
}

/* ------------------------------------------------------------ transformers */

function buildIndex() {
  const bySrc = new Map();
  for (const d of MANIFEST) bySrc.set(d.src, d);
  return bySrc;
}

function resolveTarget(srcRepoPath, target) {
  // returns a repo-relative posix path, no leading slash
  const dir = path.posix.dirname(srcRepoPath);
  const joined = target.startsWith("/") ? target.slice(1) : path.posix.join(dir, target);
  return path.posix.normalize(joined).replace(/^(\.\.\/)+/, "");
}

function rewriteLinks(text, doc, index) {
  // images first: ![alt](src)
  text = text.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (m, alt, src) => {
    if (/^(https?:)?\/\//.test(src) || src.startsWith("data:")) return m;
    const rel = resolveTarget(doc.src, src);
    const abs = path.join(REPO, rel);
    if (exists(abs) && !isDir(abs)) {
      const dest = path.join(ASSET_DIR, doc.group, path.basename(rel));
      report.imagesCopied.push({ from: rel, to: dest });
      if (!DRY) {
        fssync.mkdirSync(path.dirname(dest), { recursive: true });
        fssync.copyFileSync(abs, dest);
      }
      return `![${alt}](/docs-assets/${doc.group}/${path.basename(rel)})`;
    }
    report.imagesDropped.push({ doc: doc.src, src });
    return alt ? `*${alt}*` : "";
  });

  // links: [text](target)
  return text.replace(/\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (m, label, target) => {
    if (/^(https?:|mailto:|tel:)/i.test(target) || target.startsWith("#")) return m;
    const hashIdx = target.indexOf("#");
    const hash = hashIdx >= 0 ? target.slice(hashIdx) : "";
    const file = hashIdx >= 0 ? target.slice(0, hashIdx) : target;
    if (!file) return m;
    const rel = resolveTarget(doc.src, file);
    const hit = index.get(rel);
    if (hit) {
      report.internal++;
      return `[${label}](/docs/${hit.group}/${hit.slug}${hash})`;
    }
    const abs = path.join(REPO, rel);
    if (exists(abs)) {
      report.github++;
      const kind = isDir(abs) ? "tree" : "blob";
      return `[${label}](${GITHUB}/${kind}/main/${rel}${hash})`;
    }
    report.unlinked.push({ doc: doc.src, target });
    return label;
  });
}

function escapePlaceholders(text, doc) {
  return text.replace(/<([A-Za-z][\w.:/-]*)>/g, (m, name) => {
    if (HTML_TAGS.has(name.toLowerCase())) return m;
    if (/^https?:\/\//i.test(name)) return m; // autolink
    report.escaped.set(m, (report.escaped.get(m) || 0) + 1);
    return `&lt;${name}&gt;`;
  });
}

function fixBullets(text) {
  return text
    .split("\n")
    .map((line) => {
      if (/^\s*⸻\s*$/.test(line)) return "---";
      let m = line.match(/^\t+[•·]\s*(.*)$/);
      if (m) { report.bulletsFixed++; return `- ${m[1]}`; }
      m = line.match(/^\t+(\d+)\.\s*(.*)$/);
      if (m) { report.bulletsFixed++; return `${m[1]}. ${m[2]}`; }
      m = line.match(/^\t+(\S.*)$/);
      if (m && !/^\s*[-*]/.test(m[1])) { report.bulletsFixed++; return m[1]; } // stray tab-indented prose
      return line;
    })
    .join("\n");
}

/* ----------------------------------------------------- structure recovery */
// Some repository docs lost every code fence and heading marker after their first
// code block (an export artefact present since the initial import). What remains:
// "⸻" separators, "2) Title" / "3.1 Title" heading lines, tab-"•" bullets, and bare
// code lines. This pass rebuilds fences, headings, rules and lists heuristically.

const CODE_START = /^(\$ |python3? |pip3? |pnpm |npm |npx |yarn |curl |wget |export |cd |git |animica\b|omni\b|docker |make\b|cargo |node |deno |source |chmod |mkdir |rm |cat |echo |ls |sqlite3 |jq |grep |tail |head |sed |awk |sort |uniq |xargs |find |ps |kill |systemctl |journalctl |tar |unzip |cp |mv |touch |ssh |scp |wc |tee |watch |nvidia-smi|sudo |apt |apt-get |brew |conda |uvicorn |gunicorn |ops\/|\.\/|def |from |import |class |return\b|async |await |const |let |var |function |interface |type |if |elif |else:|for |while |try:|except|with |assert |raise |yield |print\(|console\.|module\.|export default|@\w|[{}\[\]"']|\/\/ |<[!\/a-zA-Z])/;
const BOX_CHARS = /[─-╿■-◿]/;
const YAML_LINE = /^[a-z_][a-z0-9_.-]*\s*:\s*(\S.*)?$/;
const ASSIGN = /^[A-Za-z_][\w.\[\]"']*\s*(=|\+=|-=|:=)\s*\S/;

function isCodeLine(l) {
  if (!l.trim()) return false;
  if (/^\|/.test(l) && !BOX_CHARS.test(l)) return false; // markdown table
  if (/^\s{2,}\S/.test(l)) return true;
  if (CODE_START.test(l)) return true;
  if (BOX_CHARS.test(l)) return true;
  if (/\\$|[{(]$/.test(l)) return true;
  if (ASSIGN.test(l) && !/[.!?]$/.test(l)) return true;
  if (YAML_LINE.test(l) && !/[.!?]$/.test(l)) return true;
  if (/^\w+\(.*\)\s*$/.test(l)) return true;
  if (/\S {3,}\S/.test(l) && !/[.!?:]$/.test(l)) return true; // column-aligned diagram text
  return false;
}

function guessLang(first) {
  if (/^(\$ |python|pip|pnpm|npm|npx|yarn|curl|wget|export |cd |git |animica|omni|docker|make|cargo|node|deno|source|chmod|mkdir|rm |cat |echo|ls |sqlite3|jq|ops\/|\.\/)/.test(first)) return "bash";
  if (/^(def |from |import \w|class |@|async def)/.test(first)) return "python";
  if (/^(const |let |var |function |interface |type |import \{|export )/.test(first) || /=>/.test(first)) return "ts";
  if (/^\s*[{\[]/.test(first) || /^\s*"/.test(first)) return "json";
  if (YAML_LINE.test(first)) return "yaml";
  return "text";
}

function recoverStripped(lines) {
  // 1) classify
  const kind = lines.map((raw) => {
    const l = raw.replace(/\s+$/, "");
    if (!l.trim()) return { k: "blank", t: "" };
    if (/^\s*⸻\s*$/.test(l)) return { k: "text", t: "---" };
    let m;
    if ((m = l.match(/^(\d+)\)\s+(\S.*)$/)) && !/[=;{]/.test(m[2])) return { k: "text", t: `## ${m[1]}) ${m[2]}` };
    if ((m = l.match(/^(\d+\.\d+)\)?\s+([A-ZΑ-Ω].*)$/)) && !/[=;{]/.test(m[2]) && !/[.!?]$/.test(m[2])) return { k: "text", t: `### ${m[1]} ${m[2]}` };
    if ((m = l.match(/^\t+[•·]\s*(.*)$/))) return { k: "text", t: `- ${m[1]}` };
    if ((m = l.match(/^\t+(\d+)\.\s*(.*)$/))) return { k: "text", t: `${m[1]}. ${m[2]}` };
    if ((m = l.match(/^\t+(\S.*)$/))) return { k: "text", t: m[1] };
    if (/^#/.test(l)) return { k: "comment", t: l };
    if (isCodeLine(l)) return { k: "code", t: l };
    return { k: "text", t: l };
  });

  // 2) comments join an adjacent code run; short title lines become H3s
  const prevNonBlank = (i) => { for (let j = i - 1; j >= 0 && i - j <= 2; j--) if (kind[j].k !== "blank") return kind[j]; return null; };
  const nextNonBlank = (i) => { for (let j = i + 1; j < kind.length && j - i <= 2; j++) if (kind[j].k !== "blank") return kind[j]; return null; };
  // propagate along comment chains (a block of "# …" lines above a listing) until stable
  for (let pass = 0, changed = true; changed && pass < 50; pass++) {
    changed = false;
    for (let i = 0; i < kind.length; i++) {
      if (kind[i].k !== "comment") continue;
      const p = prevNonBlank(i), n = nextNonBlank(i);
      if ((p && p.k === "code") || (n && n.k === "code")) { kind[i].k = "code"; changed = true; }
    }
  }
  for (let i = 0; i < kind.length; i++) {
    if (kind[i].k === "comment") kind[i].k = "text"; // lone comment-looking prose; keep as text
    if (kind[i].k !== "text") continue;
    const t = kind[i].t;
    const isTitle = /^[A-Z][A-Za-z0-9 &/'’,()\-]{2,60}$/.test(t) && !/^(## |### |- |---)/.test(t) && (t.split(/\s+/).length >= 2 || t.length >= 8);
    const p = i === 0 || kind[i - 1].k === "blank";
    const n = i === kind.length - 1 || kind[i + 1].k === "blank" || (kind[i + 1].k === "text" && /^(- |\d+\. )/.test(kind[i + 1].t));
    if (isTitle && p && n && !/[.!?:]$/.test(t)) kind[i].t = `### ${t}`;
  }

  // 3) group code runs (single blank lines allowed inside a run) into fences
  const out = [];
  let i = 0;
  while (i < kind.length) {
    if (kind[i].k !== "code") { out.push(kind[i].t); i++; continue; }
    let j = i;
    let last = i;
    while (j < kind.length) {
      if (kind[j].k === "code") { last = j; j++; continue; }
      if (kind[j].k === "blank" && j + 1 < kind.length && kind[j + 1].k === "code") { j++; continue; }
      break;
    }
    const block = kind.slice(i, last + 1).map((x) => x.t);
    const firstReal = block.find((l) => l.trim() && !/^\s*#/.test(l)) ?? block[0];
    const lang = /^\s*#/.test(block[0]) && /^\s*(pip|python|export|cd |git |npm|pnpm|curl|sudo|apt)/.test(firstReal) ? "bash" : guessLang(firstReal.trim());
    out.push("```" + (/^\s*<[!a-zA-Z]/.test(firstReal) ? "html" : lang), ...block, "```");
    i = last + 1;
  }
  return out;
}

function repairStrippedStructure(md, doc) {
  const lines = md.split("\n");
  let inCode = false, openIdx = -1, fence = null;
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^\s*(`{3,}|~{3,})/);
    if (!m) continue;
    if (!inCode) { inCode = true; openIdx = i; fence = m[1][0]; }
    else if (m[1][0] === fence) { inCode = false; openIdx = -1; }
  }
  if (!inCode) return md;
  const head = lines.slice(0, openIdx);
  const tail = lines.slice(openIdx + 1);
  const fixed = recoverStripped(tail);
  report.repaired.push(doc.src);
  return head.concat(fixed).join("\n");
}

function warnRawHtml(text, doc) {
  const m = text.match(/<(script|style|iframe|object|embed|link|meta)\b/i);
  if (m) report.rawHtmlWarnings.push({ doc: doc.src, tag: m[0] });
}

function processDoc(doc, index) {
  const abs = path.join(REPO, doc.src);
  let md = fssync.readFileSync(abs, "utf8").replace(/\r\n/g, "\n");

  // normalise the wrong/placeholder clone URLs found in the docs
  md = md.replace(/github\.com\/animica\/all(\.git)?/g, "github.com/animicaorg/all$1");
  md = md.replace(/https:\/\/example\.com\/animica\/animica\.git/g, "https://github.com/animicaorg/all.git");

  md = repairStrippedStructure(md, doc);

  // title = first H1
  const h1 = md.match(/^# (.+)$/m);
  if (!h1) throw new Error(`${doc.src}: no H1 found`);
  const title = h1[1].replace(/[`*_]/g, "").trim();
  md = md.slice(0, h1.index) + md.slice(h1.index + h1[0].length);

  const segs = splitFences(md);
  const out = segs.map((seg) => {
    if (seg.code) return seg.text;
    let t = seg.text;
    // demote extra H1s
    t = t.replace(/^# (.+)$/gm, (m, h) => { report.demotedH1++; return `## ${h}`; });
    t = fixBullets(t);
    t = mapOutsideInlineCode(t, (plain) => escapePlaceholders(plain, doc));
    t = mapOutsideInlineCode(t, (plain) => rewriteLinks(plain, doc, index));
    warnRawHtml(t, doc);
    return t;
  }).join("\n").replace(/^\n+/, "").replace(/\n{3,}/g, "\n\n");

  const notes = [];
  notes.push(`*Source: \`${doc.src}\` — this page mirrors the repository documentation.*`);
  if (/dilithium|sphincs/i.test(out)) notes.push(NOTE_PQ);
  if (/faucet/i.test(out) || (out.match(/devnet/gi) || []).length >= 3) notes.push(NOTE_DEVNET);

  const body = `${notes.join("\n\n")}\n\n${out.trim()}\n`;
  const wc = words(out);
  if (wc < MIN_WORDS) throw new Error(`${doc.src}: only ${wc} words after processing (< ${MIN_WORDS})`);

  const fm = [
    "---",
    `title: ${JSON.stringify(title)}`,
    `description: ${JSON.stringify(doc.description)}`,
    `group: ${JSON.stringify(doc.group)}`,
    `order: ${doc.order}`,
    "draft: false",
    "---",
  ].join("\n");

  return { title, words: wc, content: `${fm}\n\n${body}` };
}

/* ------------------------------------------------------------------- main */

(async function main() {
  const index = buildIndex();
  const groupKeys = new Set(GROUPS.map((g) => g.key));
  for (const d of MANIFEST) {
    if (!groupKeys.has(d.group)) throw new Error(`${d.src}: unknown group ${d.group}`);
    if (!exists(path.join(REPO, d.src))) throw new Error(`missing source: ${d.src}`);
  }
  const seen = new Set();
  for (const d of MANIFEST) {
    const k = `${d.group}/${d.slug}`;
    if (seen.has(k)) throw new Error(`duplicate slug ${k}`);
    seen.add(k);
  }

  if (!DRY) {
    await fs.rm(OUT_DIR, { recursive: true, force: true });
    await fs.mkdir(OUT_DIR, { recursive: true });
    // Underscore-prefixed files are ignored by content collections; the pages import this.
    await fs.writeFile(path.join(OUT_DIR, "_groups.json"), JSON.stringify(GROUPS, null, 2) + "\n", "utf8");
  }

  const perGroup = new Map();
  let total = 0;
  for (const d of MANIFEST) {
    const r = processDoc(d, index);
    const dest = path.join(OUT_DIR, d.group, `${d.slug}.md`);
    if (!DRY) {
      await fs.mkdir(path.dirname(dest), { recursive: true });
      await fs.writeFile(dest, r.content, "utf8");
    }
    report.files.push({ src: d.src, dest: path.relative(SITE_ROOT, dest), words: r.words, title: r.title });
    perGroup.set(d.group, (perGroup.get(d.group) || 0) + 1);
    total += r.words;
  }

  console.log(`synced ${report.files.length} docs, ${total} words${DRY ? " (dry run)" : ""}`);
  for (const g of GROUPS) console.log(`  ${g.key.padEnd(16)} ${perGroup.get(g.key) || 0}`);
  console.log(`links: ${report.internal} internal, ${report.github} → GitHub, ${report.unlinked.length} unlinked (missing targets)`);
  for (const u of report.unlinked) console.log(`  unlinked: ${u.doc} → ${u.target}`);
  console.log(`images: ${report.imagesCopied.length} copied, ${report.imagesDropped.length} dropped`);
  console.log(`demoted H1s: ${report.demotedH1}; tab-bullets fixed: ${report.bulletsFixed}`);
  console.log(`structure-recovered (orphan fence): ${report.repaired.length} docs`);
  for (const f of report.files) {
    const txt = fssync.existsSync(path.join(SITE_ROOT, f.dest)) ? fssync.readFileSync(path.join(SITE_ROOT, f.dest), "utf8") : "";
    const n = (txt.match(/^\s*```/gm) || []).length;
    if (n % 2) console.log(`  ERROR unbalanced fences in ${f.dest}`);
  }
  if (report.escaped.size) {
    console.log(`escaped placeholders: ${[...report.escaped.entries()].map(([k, v]) => `${k}×${v}`).join(" ")}`);
  }
  for (const w of report.rawHtmlWarnings) console.log(`  WARNING raw html outside code: ${w.doc} (${w.tag})`);
  const smallest = [...report.files].sort((a, b) => a.words - b.words).slice(0, 3);
  console.log(`smallest: ${smallest.map((f) => `${f.src} (${f.words}w)`).join(", ")}`);
})().catch((e) => {
  console.error("sync_curated_docs failed:", e?.stack || e);
  process.exit(1);
});
