/*
 * Animica network-upgrade notice bar.
 * Central, self-contained, cross-origin-includable. One
 *   <script defer src="https://animica.dev/anm-upgrade-banner.js"></script>
 * on any site renders a fixed bottom notice. To retire it everywhere after the
 * upgrade, empty this file — no per-site change needed.
 *
 * Height-gated facts, verified against deployed core.network_params /
 * execution.migrations / consensus.rewards:
 *   block 44,444 — 7.1.9 consensus activation (treasury-scam clawback, a
 *     STATE-MUTATING migration + address freeze). A node not on >=7.1.9 keeps
 *     the scam accounts funded and SILENTLY DIVERGES from network balances.
 *   animica 7.2.0 (current release, supersedes 7.1.9 — contains everything in
 *     it) additionally fixes the 38,728 fork-wedge: nodes that took the losing
 *     side of the 2026-07-07 one-block fork sat stuck at height 38,728 forever;
 *     7.2.0 self-heals them at restart (pinned canonical checkpoint + sync
 *     fork-sibling ingest) and repairs snapshot bootstrap.
 * DEADLINE is 70,000 again — a REAL mandatory activation, so the CTA is the upgrade
 *   guide and the pre-activation branch carries the countdown. NOTE the branches:
 *   height < DEADLINE renders the PRE branch, and it had been left carrying the
 *   retired 50,000 IOU copy while the new text sat in the post-activation branch
 *   nobody would see until it was already too late. Check which branch renders at
 *   the current height before shipping banner copy.
 * Current release: animica 10.2.5. CONSENSUS FLOOR IS ALSO 10.2.5 — two separate reasons
 *   stack here. 9.7.0 was the release that WIRED the 75,000 forks (they were
 *   defined-but-dead in network_params before it — the node computed the old split and
 *   reverted the new rules), so every pre-9.7.0 node is non-compliant at 75,000. Then
 *   10.2.5 changed the carve PAYOUT rule: a block in which ANY provider claims now pays
 *   the WHOLE inference slice to those providers pro-rata, rather than paying only the
 *   claimed amount and rolling the remainder to the treasury. A 10.2.4 node computes the
 *   old amounts, so the two disagree on balances the first time a settlement anchor
 *   lands. INERT until then: with no anchors in a block both versions are byte-identical,
 *   which is why this could ship close to the height — but do not post an anchor until
 *   the fleet is on 10.2.5.
 * ONE MANDATORY CONSENSUS HEIGHT, 75,000. FORK_TREASURY_25 was once slated for 70,000 but
 *   the reward code never read the flag there, so nothing activated and no realized history
 *   changed; 9.7.0 moves it to 75,000 so the whole reward-split change lands in one step.
 *   75,000 — the split becomes 50% miner / 25% treasury / 25% inference:
 *     FORK_TREASURY_25 raises the treasury 15% -> 25%, and FORK_SERVICE_CARVE reserves
 *     25% of every block for inference, WITHHELD FROM THE MINER whether or not anything
 *     claims it (miner 85% -> 50%). Unclaimed inference rolls to the foundation treasury
 *     (escrow == treasury in the wiring), so a no-claim block is 50/50 and any unclaimed
 *     remainder on a partly-claimed block also goes to the treasury.
 *     Also at 75,000: on-chain contract execution (FORK_VM_EXEC — the token launcher +
 *     DEX), value-carrying CALL, bounded difficulty retarget, uniform reorg bound, and
 *     quantum-beacon binding (optional/dormant by design). All were unwired before 9.7.0.
 *   COUNTDOWN: mount() counts to the NEXT pending height, not always DEADLINE — a
 *     countdown to 75,000 while 70,000 is still ahead understates urgency by ~2,000 blocks.
 *   BANNER SELF-CHECK: this file once carried a stray copy fragment ABOVE the IIFE that
 *     referenced `count`, so the whole script threw ReferenceError on load and NO banner
 *     rendered on any site for a while. `node --check` passes on that file — it is
 *     syntactically fine. Always EXECUTE it against a fake DOM before shipping.
 * Superseded headline: animica 9.4.0 — MANDATORY CONSENSUS UPGRADE at block 70,000.
 *   FORK_TREASURY_25: the foundation-treasury share of every block subsidy goes from
 *   15% to 25% (miner 85% -> 75%). The subsidy TOTAL and the halving schedule are
 *   unchanged, so emission and MAX_MONEY are untouched — only the division changes.
 *   HOW AN UN-UPGRADED NODE FAILS — corrected 2026-08-09, the first description was
 *   wrong. There is NO rejection and NO fork. `_compute_block_reward_amount`
 *   (block_import.py:702) has ZERO callers and the only coinbase validation
 *   (_validate_coinbase_outputs_nonzero, :386) checks solely for a zero-address
 *   output — coinbase AMOUNTS are never checked against compute_block_reward. But
 *   state application credits balances from the node's OWN compute_block_reward
 *   (block_import.py:~2796 reward_outputs), not from the block's coinbase tx. So a
 *   9.3.x node accepts byte-identical blocks and credits a 15% treasury forever:
 *   a SILENT, PERMANENT ledger divergence with no orphan, no stall and no error.
 *   That is quieter than a fork and therefore more dangerous for anyone reading
 *   balances. Every full node, pool and balance-tracking exchange must run >=9.4.0
 *   (or set ANIMICA_FORK_TREASURY_25_HEIGHT) BEFORE 70,000.
 * Previous headline: animica 9.3.2 — THE CLI IS AN AGENTIC CODING ASSISTANT (non-consensus).
 *   `animica chat` reads, edits and runs code in the directory you start it in, with four
 *   approval modes, parallel /swarm agents, piped stdin, and AGENTS.md support. It needs no API
 *   key, no wallet and no GPU: inference is kimi-k3 served by the miner network. Free tier is
 *   unlimited chat + 10 agentic tasks/day; paid plans lift the caps (animica.dev/#pricing).
 *   Nothing here is consensus-affecting, so no node MUST take it — but it SUPERSEDES 9.2.0 and
 *   carries every fix below, so `pip install -U animica` is still the right command for a miner
 *   who wants per-share payouts. Previous headline:
 * animica 9.2.0 — SUB-BLOCK SHARES (re-enabled; all review findings closed) (non-consensus; miners + pool operators).
 *   The wire share target is a RATIO of θ and the xmrig-compat floor pinned it at 1.0 == θ == the
 *   block target, so a "share" WAS a block and PPS paid only block finders. 9.1.0 hands sub-block
 *   (9.1.1: every shipped miner opts in automatically + `animica up` reports it) targets to sessions that opt in on mining.subscribe (features.subblockShares) — derived per job
 *   as r = 1 - MICRO*ln(S)/θ so a share is worth 1/S of a block and the pool-wide share rate is
 *   S/block_time. Every client that does NOT opt in (xmrig, ASIC, cryptonote, older miners) keeps
 *   byte-for-byte the target it got before. Carries 9.0.9/9.0.10: 5% block-reserve so credit-cap
 *   headroom is never fully drained by the winner, per-IP stratum connection cap + idle-session
 *   reaper (one client held 835 idle sockets => "838 miners"), honest num_miners vs num_connections,
 *   and a defensive miner-side set_difficulty parser (an unparseable push used to kill the miner).
 * Previous: animica 9.0.8/9.0.6 — RPC unbounded-scan fix (node stall), see git history.
 *   rpc/methods/tx.py + receipt.py fell back to an UNBOUNDED linear block scan (4096 deep)
 *   whenever the tx/receipt index missed — i.e. for any pending/unknown txid, exactly what
 *   status pollers ask about. ~4.4ms/block => ~18s of SQLite-lock-holding CPU per miss; a few
 *   pollers occupied every RPC worker thread and starved miner.getBlockTemplate, so the pool
 *   could not build jobs and BLOCK PRODUCTION STALLED (mainnet froze at 54262 for ~57min).
 *   9.0.6 bounds both scans to 128 (env-overridable). getBlockTemplate 20s -> 29ms.
 *   Carries 9.0.5 (mining sync-gate: a peer's false height can no longer halt production),
 *   9.0.4 (p2p dial/handshake timeouts) and the 9.0.1 LTS line.
 *   No genesis reset / no state change — safe-for-all; hosted-service & wallet users need do nothing.
 * Pre-activation: mandatory-upgrade bar (7.1.9 campaign @ 44,444). Post-activation
 * (height >= 44,444, the case today): the 44,854 stuck-node remedy notice, until
 * RETIRE_STUCK.
 * Balance-tracking exchanges/explorers and pools/miners MUST be on >=7.1.9
 * before 44,444 — install 7.2.0. Ordinary users of hosted services and
 * wallets need do nothing — the copy says so, so they self-select out.
 */
(function () {
  "use strict";
  if (window.__anmUpgradeBanner) return;                 // idempotent
  window.__anmUpgradeBanner = true;

  // ONE mandatory height now. FORK_TREASURY_25 was originally scheduled for 70,000 but
  // the reward code never read the flag there, so nothing activated at 70,000 and no
  // realized history changed; 9.7.0 moves it to 75,000 so the WHOLE reward-split change
  // lands in one coordinated step alongside the other 9.5.x/9.6.0 forks. DEADLINE_FIRST
  // is kept equal to DEADLINE so the single-height copy renders for every reader.
  var DEADLINE = 75000;                                   // all forks activate here — MANDATORY
  var DEADLINE_FIRST = DEADLINE;
  var VERSION = "11.1.0";
  // The lowest release that computes post-75,000 state correctly. 9.7.0 is the floor:
  // block 75,000 activates the treasury 15%->25% raise AND the 25% inference carve
  // (emission split), value-carrying CALL and on-chain contract execution (state), and
  // the bounded difficulty retarget (theta). All were defined-but-unwired before 9.7.0,
  // so a node below it computes the OLD 85/15 split and a divergent state root the moment
  // the fork height passes. 9.7.0 supersedes the 9.6.0 VM floor and carries it.
  // RAISED TO 10.2.5 (was 9.7.1). 10.2.5 changes the carve payout rule itself: from
  // 75,000, a block in which ANY provider claims pays the WHOLE inference slice to those
  // providers pro-rata, instead of paying only the claimed amount and sending the
  // remainder to the treasury. A 10.2.4 node computes the old amounts, so the two
  // disagree on balances the first time a settlement anchor lands. It is INERT until
  // then — with no anchors both versions are identical — which is why the release can
  // ship this close to the height, but every node must still be on 10.2.5 before any
  // anchor is posted.
  var CONSENSUS_MIN = "10.2.5";
  // Separate from the consensus floor and NOT the same number. 10.2.2 restored the
  // getBlockTemplate carve fix (7281d06c, reverted by the 10.1.0 commit): below it the
  // template advertises the PRE-carve miner slice, so from 75,000 it says 225 ANM where
  // the miner receives 150. Blocks stay valid — submitBlock prefers state_balance_delta —
  // so this is a payout bug, not a fork: a pool crediting shares from the advertised value
  // hands out 75 ANM/block of its own money. Node operators who do not run a pool are
  // unaffected by this one, which is why it is not folded into CONSENSUS_MIN.
  var POOL_MIN = "10.2.2";
  var NOTICE = "https://animica.dev/upgrade/";            // a consensus deadline: the guide is the action
  var GUIDE = "https://animica.dev/upgrade/";             // still the right link for node operators
  var HEIGHT_URL = "https://animica.dev/net-height";
  var KEY = "anmUpgrade-1110";                             // re-show: 10.3.1 is the current release (compute/inference + payout wallet-clamp fix); CONSENSUS FLOOR stays 10.2.5
  // Fail-safe retirement: if live height is never readable (cross-origin/CORS/network),
  // still stop showing a pre-activation notice after this date. Height stays the
  // authoritative deadline; this only ever HIDES the bar, so it can't misfire in the
  // dangerous direction.
  var RETIRE_AFTER = Date.parse("2026-12-01T00:00:00Z");
  // 8.0.0 feature-release notice (non-consensus) retires on this date.
  var RETIRE_STUCK = Date.parse("2026-11-15T00:00:00Z");

  try { if (location.host === "animica.dev" && /^\/upgrade\/?$/.test(location.pathname)) return; } catch (e) {}
  try { if (sessionStorage.getItem(KEY) === "1") return; } catch (e) {}
  try { if (RETIRE_AFTER && Date.now() > RETIRE_AFTER) return; } catch (e) {}

  var bar = null, prevPad = null;

  function el(tag, css, html) {
    var n = document.createElement(tag);
    if (css) n.style.cssText = css;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function setOffset() {              // keep the fixed bar from covering page content/nav
    try { if (bar && document.body) {
      if (prevPad === null) prevPad = document.body.style.paddingBottom || "";
      document.body.style.paddingBottom = (bar.offsetHeight || 54) + "px";
    } } catch (e) {}
  }
  function clearOffset() {
    try { if (document.body && prevPad !== null) { document.body.style.paddingBottom = prevPad; prevPad = null; } } catch (e) {}
  }
  function onResize() { if (bar) setOffset(); }
  function teardown() {
    if (bar && bar.parentNode) bar.parentNode.removeChild(bar);
    bar = null;
    clearOffset();
    window.removeEventListener("resize", onResize);
  }

  function mount(blocksLeft, postActivation, nextHeight) {
    if (document.getElementById("anm-upgrade-bar")) return;

    bar = el("div", [
      "position:fixed;left:0;right:0;bottom:0;z-index:2147483000",
      "font-family:'Inter',system-ui,-apple-system,Segoe UI,Roboto,sans-serif",
      "background:#05140F;color:#E8F4EE;border-top:2px solid #FFC24B",
      "box-shadow:0 -10px 30px rgba(3,6,20,.45);box-sizing:border-box"
    ].join(";"));
    bar.id = "anm-upgrade-bar";
    bar.setAttribute("role", "region");
    bar.setAttribute("aria-label", "Animica network upgrade notice");

    var wrap = el("div",
      "max-width:1180px;margin:0 auto;padding:11px 16px;display:flex;align-items:center;gap:14px;flex-wrap:wrap");

    // Official Animica mark (the downloadable-wallet app icon): blue orb + "A".
    var mark = el("span", "flex:0 0 auto;display:inline-flex",
      "<svg width='26' height='26' viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg' aria-hidden='true'>" +
      "<circle cx='128' cy='128' r='112' fill='#2E63FF'/>" +
      "<circle cx='128' cy='128' r='88' fill='#FFFFFF'/>" +
      "<path d='M128 68 L84 192 H172 L128 68 Z' fill='#2E63FF'/>" +
      "<rect x='104' y='160' width='48' height='16' rx='8' fill='#2E63FF'/></svg>");

    // Blocks remaining to the activation height, when the live height is readable.
    // A consensus deadline with a number on it gets acted on; one without does not.
    var count = (typeof blocksLeft === "number" && blocksLeft > 0)
      ? " <span style=\"font-family:'JetBrains Mono',ui-monospace,monospace;color:#FFC24B\">(~"
        + blocksLeft.toLocaleString() + " blocks left)</span>"
      : "";
    var msgHtml;
    if (postActivation) {
      msgHtml =
        "<strong style='color:#FFC24B'>New: animica 11.1.0</strong> &mdash; accurate image generation "
        + "(prompt compiler + fidelity judge), a video director with distributed shots, web-grounded chat, "
        + "and a working <span style=\"font-family:'JetBrains Mono',ui-monospace,monospace\">wallet new</span>. "
        + "<strong style='color:#fff'>11.0.0's CLI does not start</strong> &mdash; skip straight to " + VERSION + ": "
        + "<span style=\"font-family:'JetBrains Mono',ui-monospace,monospace;color:#14C79B\">pip install -U animica</span>. "
        + "<strong style='color:#FFC24B'>New: Serve &amp; Earn</strong> &mdash; your phone or laptop answers AI jobs "
        + "straight from the browser and earns ANM per job, paid on-chain every block: "
        + "<a href='https://pool.animica.org/serve' style='color:#14C79B;text-decoration:underline'>pool.animica.org/serve</a>. "
        + "<span style='color:#A9C4B8'>Still below " + CONSENSUS_MIN + "? Your node has computed wrong balances since "
        + "block 75,000 with no visible symptom &mdash; update now (pools below " + POOL_MIN + " overpay 75 ANM/block). "
        + "Wallet and hosted-service users: nothing to do.</span>";
    } else {
      // ONE mandatory height (75,000) now — see the DEADLINE note above. Every fork,
      // including the treasury raise once slated for 70,000, activates here in one step.
      var head = "Mandatory consensus upgrade at block 75,000.";
      msgHtml =
        "<strong style='color:#FFC24B'>" + head + "</strong> "
        + "Update to animica <strong style='color:#fff'>" + VERSION + "</strong>. At 75,000 the "
        + "foundation-treasury share goes from <strong style='color:#fff'>15% to 25%</strong> and the block "
        + "also reserves <strong style='color:#fff'>25% of every block for inference payments</strong>, "
        + "withheld from the miner whether or not anything claims it &mdash; so the split becomes "
        + "<strong style='color:#fff'>50% miner / 25% treasury / 25% inference</strong> (miners go from "
        + "85% to 50%). Where the inference slice lands depends on one thing &mdash; whether any provider "
        + "claims in that block. Nothing claimed: it goes to the treasury, so the treasury receives 50% of "
        + "that block. <strong style='color:#fff'>Any claim: the WHOLE slice is paid to the claiming "
        + "providers</strong>, split pro-rata, and the treasury takes no share of it. "
        + "The same height also turns on on-chain contract execution "
        + "(the token launcher + DEX), value-carrying CALL, a bounded difficulty retarget and a uniform "
        + "reorg bound. Total emission and the halving schedule are "
        + "<strong style='color:#fff'>unchanged</strong> &mdash; only the split. "
        + "<span style=\"font-family:'JetBrains Mono',ui-monospace,monospace;color:#14C79B\">pip install -U animica</span> "
        + "on every <strong style='color:#fff'>full node, pool and exchange</strong>." + count
        + " Then CHECK IT TOOK: "
        + "<span style=\"font-family:'JetBrains Mono',ui-monospace,monospace;color:#14C79B\">animica --version</span>"
        + " must report <strong style='color:#fff'>" + VERSION + "</strong> &mdash; if it does not, the "
        + "release has not reached your index yet and you are NOT upgraded. "
        + "Skipping it breaks two separate things, and <strong style='color:#fff'>neither announces itself</strong>. "
        + "<strong style='color:#fff'>Below " + CONSENSUS_MIN + "</strong>: the node computes the OLD split and will "
        + "<strong style='color:#fff'>not fork, stall or error</strong> &mdash; coinbase amounts are not validated "
        + "against the schedule, so it accepts the same blocks and reports "
        + "<strong style='color:#fff'>WRONG BALANCES, silently and permanently</strong>. "
        + "<strong style='color:#fff'>Below " + POOL_MIN + "</strong>: a <strong style='color:#fff'>pool</strong> "
        + "overpays &mdash; the block template will advertise 225 ANM while the miner actually receives 150, so a "
        + "pool crediting shares from that figure hands out <strong style='color:#fff'>75 ANM per block out of its "
        + "own hot wallet</strong>. "
        + "<span style='color:#A9C4B8'>Wallet and hosted-service users: nothing to do.</span>";
    }
    var msg = el("div", "flex:1 1 320px;font-size:14px;line-height:1.45", msgHtml);

    var cta = el("a", [
      "flex:0 0 auto;text-decoration:none;font-weight:600;font-size:13.5px",
      "padding:8px 15px;border-radius:9px;color:#FFFFFF",
      "background:#2E63FF"
    ].join(";"), "Upgrade guide →");
    cta.href = NOTICE;

    var x = el("button",
      "flex:0 0 auto;background:transparent;border:1px solid #1D5340;color:#A9C4B8;border-radius:8px;width:30px;height:30px;cursor:pointer;font-size:15px;line-height:1", "&times;");
    x.setAttribute("aria-label", "Dismiss for this session");
    x.onclick = function () { try { sessionStorage.setItem(KEY, "1"); } catch (e) {} teardown(); };

    wrap.appendChild(mark);
    wrap.appendChild(msg);
    wrap.appendChild(cta);
    wrap.appendChild(x);
    bar.appendChild(wrap);
    (document.body || document.documentElement).appendChild(bar);

    setOffset();
    window.addEventListener("resize", onResize);
  }

  function ready(fn) {
    if (document.body) fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }
  function decideAndMount(height) {
    if (typeof height === "number" && height >= DEADLINE) {
      // Post-activation: keep a lighter notice for wedged-node operators for a
      // couple of weeks, then retire fully.
      if (Date.now() > RETIRE_STUCK) return;
      ready(function () { mount(null, true); });
      return;
    }
    // Count down to the NEXT pending activation, not always the last one. There are two
    // mandatory heights in this campaign (70,000 and 75,000) and a countdown to 75,000
    // while 70,000 is still ahead understates the urgency by ~2,000 blocks. Whichever is
    // still in front is the one an operator has to act on.
    var next = (typeof height === "number" && height < DEADLINE_FIRST) ? DEADLINE_FIRST : DEADLINE;
    var left = (typeof height === "number" && height < next) ? (next - height) : null;
    ready(function () { mount(left, false, next); });
  }

  // Try live height (CORS-enabled JSON {height:N}); render statically if it fails.
  var done = false, t = setTimeout(function () { if (!done) { done = true; decideAndMount(null); } }, 2500);
  try {
    fetch(HEIGHT_URL, { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (done) return; done = true; clearTimeout(t);
        var h = d && (d.height != null ? d.height : (d.result && d.result.height));
        decideAndMount(typeof h === "number" ? h : null);
      })
      .catch(function () { if (!done) { done = true; clearTimeout(t); decideAndMount(null); } });
  } catch (e) { if (!done) { done = true; clearTimeout(t); decideAndMount(null); } }
})();
