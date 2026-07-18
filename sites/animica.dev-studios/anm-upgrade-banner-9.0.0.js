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
 * Current release: animica 8.5.2 — media safety filter + music generation fixed + all-day livestream chat (non-consensus).
 *   `animica animal stream --youtube` runs an animated character live on YouTube around the
 *   clock: it reads the live chat and replies out loud, moves and does quirky things, overlays
 *   live network stats, and auto-uploads every 1-hour segment as a VOD. Design the character by
 *   chat, upload a custom PNG mascot, and give it a private knowledge base — sold at
 *   animica.dev/animal ($350/mo). Carries forward 8.4.x network-wide AI serving + generative
 *   media (image / video / multi-scene / music, working straight from `pip install`).
 *   Superset of 8.0.6/8.0.4/8.0.2, so it STILL un-wedges nodes stuck syncing at block 44,854
 *   (pinned canonical checkpoint 44,854 -> 0x0000000004c045379a4e1d049e7b225e951aa30ee9346718155dfb57a2ec44c9).
 *   No genesis reset / no state change — safe-for-all; a no-op for nodes on the
 *   canonical chain that don't run a media/AI/VPN provider.
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

  var DEADLINE = 50000;                                   // last MANDATORY activation (7.1.9); 8.0.x+ is non-consensus
  var VERSION = "9.0.0";
  var NOTICE = "https://animica.dev/upgrade";
  var HEIGHT_URL = "https://animica.dev/net-height";
  var KEY = "anmUpgradeDismissed-900";                   // new key: re-show for 8.5.2
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

  function mount(blocksLeft, postActivation) {
    if (document.getElementById("anm-upgrade-bar")) return;

    bar = el("div", [
      "position:fixed;left:0;right:0;bottom:0;z-index:2147483000",
      "font-family:'Inter',system-ui,-apple-system,Segoe UI,Roboto,sans-serif",
      "background:#0b1224;color:#e8ecf6;border-top:2px solid #ffb020",
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

    var msgHtml;
    if (postActivation) {
      msgHtml =
        "<strong style='color:#ffb020'>New in 9.0.0:</strong> the "
        + "<strong style='color:#fff'>GPU Studios</strong> are live — video upscaling/60fps/subtitles/shorts, "
        + "audio stems &amp; mastering, and a distributed Blender render farm, all on the GPU-miner network. "
        + "This release also turns on <strong style='color:#fff'>on-chain IOU settlement</strong> at block "
        + "<strong style='color:#ffb020'>50,000</strong>: service IOUs (GPU media, dVPN relay, hosting) can be "
        + "paid straight from the block reward, capped at 50 ANM/block (&le;20% of the block). "
        + "Try it: <a href='https://animica.dev/video' style='color:#37e0d8;text-decoration:none'>animica.dev/video</a>. "
        + "Get it: <span style=\"font-family:'JetBrains Mono',ui-monospace,monospace\">pip install -U animica</span>. "
        + "<span style='color:#9aa6c4'>Node operators not yet on 9.0.0: upgrade now.</span>";
    } else {
      var count = (typeof blocksLeft === "number")
        ? " <span style=\"font-family:'JetBrains Mono',ui-monospace,monospace;color:#9aa6c4\">(~" + blocksLeft.toLocaleString() + " blocks)</span>"
        : "";
      msgHtml =
        "Every Animica <strong style='color:#fff'>full node</strong>, pool &amp; balance-tracking "
        + "exchange must upgrade to <strong style='color:#fff'>animica " + VERSION + "</strong> before block "
        + "<strong style='color:#ffb020'>50,000</strong>" + count + " — the <strong style='color:#fff'>IOU-settlement "
        + "fork</strong> (service IOUs paid from the block reward, &le;20%/block). Un-upgraded nodes diverge from "
        + "network balances once the first settlement anchor posts. 9.0.0 also opens the GPU Studios "
        + "(<a href='https://animica.dev/video' style='color:#37e0d8;text-decoration:none'>video</a> / "
        + "<a href='https://animica.dev/audio' style='color:#37e0d8;text-decoration:none'>audio</a> / "
        + "<a href='https://animica.dev/render' style='color:#37e0d8;text-decoration:none'>render</a>). "
        + "<span style='color:#9aa6c4'>Hosted-service &amp; wallet users: nothing to do.</span>";
    }
    var msg = el("div", "flex:1 1 320px;font-size:14px;line-height:1.45", msgHtml);

    var cta = el("a", [
      "flex:0 0 auto;text-decoration:none;font-weight:600;font-size:13.5px",
      "padding:8px 15px;border-radius:9px;color:#04122a",
      "background:linear-gradient(180deg,#37e0d8,#2bb6cf)"
    ].join(";"), "Upgrade guide →");
    cta.href = NOTICE;

    var x = el("button",
      "flex:0 0 auto;background:transparent;border:1px solid #24325a;color:#9aa6c4;border-radius:8px;width:30px;height:30px;cursor:pointer;font-size:15px;line-height:1", "&times;");
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
    var left = (typeof height === "number" && height < DEADLINE) ? (DEADLINE - height) : null;
    ready(function () { mount(left, false); });
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
