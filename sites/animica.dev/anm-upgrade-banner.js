/*
 * Animica network-upgrade notice bar.
 * Central, self-contained, cross-origin-includable. One
 *   <script defer src="https://animica.dev/anm-upgrade-banner.js"></script>
 * on any site renders a fixed bottom notice. To retire it everywhere after the
 * fork, empty this file — no per-site change needed.
 *
 * Height-gated facts, verified against deployed core.network_params / consensus.rewards:
 *   block 42,000 — FORK_ADDRESS_FREEZE (pip 7.0.0): a reject rule. A node not on
 *     >=7.0.0 can follow a non-canonical fork → "fork off mainnet".
 *   block 42,001 — FORK_FOUNDATION_SPLIT (pip 7.1.0): an emission re-split (85/15).
 *     A node not on >=7.1.0 stays on-chain but silently miscredits balances.
 *   Current release that includes both: animica 7.1.1.
 * Deadline shown is block 42,000 (the earliest). Ordinary users of hosted services
 * and wallets need do nothing — the copy says so, so they self-select out.
 */
(function () {
  "use strict";
  if (window.__anmUpgradeBanner) return;                 // idempotent
  window.__anmUpgradeBanner = true;

  var FREEZE = 42000, SPLIT = 42001;
  var NOTICE = "https://animica.dev/upgrade";
  var HEIGHT_URL = "https://animica.dev/net-height";
  var KEY = "anmUpgradeDismissed-v1";
  // Fail-safe retirement: if live height is never readable (cross-origin/CORS/network),
  // still stop showing a pre-fork notice after this date. Height stays the authoritative
  // deadline; this only ever HIDES the bar, so it can't misfire in the dangerous direction.
  var RETIRE_AFTER = Date.parse("2026-09-01T00:00:00Z");

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

  function mount(blocksLeft) {
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

    var count = (typeof blocksLeft === "number")
      ? " <span style=\"font-family:'JetBrains Mono',ui-monospace,monospace;color:#9aa6c4\">(~" + blocksLeft.toLocaleString() + " blocks)</span>"
      : "";
    var msg = el("div", "flex:1 1 320px;font-size:14px;line-height:1.45",
      "Every Animica <strong style='color:#fff'>full node</strong> must upgrade to " +
      "<strong style='color:#fff'>animica 7.1.1</strong> before block " +
      "<strong style='color:#ffb020'>42,000</strong>" + count + " to stay on mainnet. " +
      "<span style='color:#9aa6c4'>Hosted-service &amp; wallet users: nothing to do.</span>");

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
    // Self-expire once the split has activated — stop showing the pre-fork CTA.
    if (typeof height === "number" && height >= SPLIT) return;
    var left = (typeof height === "number" && height < FREEZE) ? (FREEZE - height) : null;
    ready(function () { mount(left); });
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
