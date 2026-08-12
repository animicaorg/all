/*
 * <anm-price-ticker> — self-contained live ANM/USDT price ticker.
 *
 * Reads the same-origin /anm-price.json feed (published by the anm-price timer),
 * so there is no cross-origin call to NonKYC (whose API sends no CORS header).
 * Shadow DOM keeps styling isolated so it drops cleanly into any site.
 *
 * Usage:
 *   <script src="/anm-ticker.js" defer></script>
 *   <anm-price-ticker></anm-price-ticker>
 * Attributes (all optional):
 *   endpoint   feed URL (default "/anm-price.json")
 *   variant    "pill" (default) | "inline" | "hero"
 *   href       override click target (default the feed's market_url)
 */
(function () {
  "use strict";
  if (customElements.get("anm-price-ticker")) return;

  var MARKET = "https://nonkyc.io/market/ANM_USDT";

  function fmtPrice(v) {
    v = Number(v);
    if (!isFinite(v) || v <= 0) return "—";
    // Small sub-cent price: show up to 8 decimals, trimmed, min 4 sig decimals.
    if (v < 1) {
      var s = v.toFixed(8).replace(/0+$/, "");
      if (s.split(".")[1] && s.split(".")[1].length < 4) s = v.toFixed(4);
      return "$" + s;
    }
    return "$" + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  }

  function fmtChg(p) {
    p = Number(p) || 0;
    return (p > 0 ? "+" : "") + p.toFixed(2) + "%";
  }

  var CSS =
    ":host{display:inline-block;font-family:inherit;line-height:1}" +
    ".w{display:inline-flex;align-items:center;gap:.5em;padding:.4em .7em;border:1px solid var(--anm-border,rgba(127,127,127,.35));" +
    "border-radius:999px;background:var(--anm-bg,rgba(127,127,127,.06));color:inherit;text-decoration:none;font-size:.9rem;" +
    "transition:border-color .15s,background .15s;white-space:nowrap}" +
    ".w:hover{border-color:var(--anm-accent,#4ade80);background:var(--anm-bg-hover,rgba(74,222,128,.08))}" +
    ".dot{width:.5em;height:.5em;border-radius:50%;background:var(--anm-accent,#4ade80);flex:0 0 auto;" +
    "box-shadow:0 0 0 0 var(--anm-accent,#4ade80);animation:anmpulse 2.4s infinite}" +
    "@keyframes anmpulse{0%{box-shadow:0 0 0 0 rgba(74,222,128,.5)}70%{box-shadow:0 0 0 .5em rgba(74,222,128,0)}100%{box-shadow:0 0 0 0 rgba(74,222,128,0)}}" +
    ".sym{font-weight:700;letter-spacing:.02em}" +
    ".px{font-variant-numeric:tabular-nums;font-weight:600}" +
    ".chg{font-variant-numeric:tabular-nums;font-size:.82em;font-weight:600;padding:.05em .4em;border-radius:.4em}" +
    ".chg.up{color:#16a34a;background:rgba(22,163,74,.12)}.chg.down{color:#dc2626;background:rgba(220,38,38,.12)}" +
    ".chg.flat{color:var(--anm-muted,#9aa0a6);background:transparent}" +
    ".tag{font-size:.72em;opacity:.7}" +
    ".ind{opacity:.85}.ind .px::before{content:'~';opacity:.7;margin-right:.05em}" +
    ":host([variant=hero]) .w{font-size:1.05rem;padding:.55em .95em}" +
    ":host([variant=inline]) .w{border:0;background:none;padding:0;gap:.35em}" +
    ":host([variant=inline]) .w:hover{background:none}";

  var proto = Object.create(HTMLElement.prototype);

  function render(el, d) {
    var market = el.getAttribute("href") || (d && d.market_url) || MARKET;
    var indicative = d && d.is_indicative;
    var chg = d ? Number(d.change_percent) || 0 : 0;
    var chgCls = chg > 0 ? "up" : chg < 0 ? "down" : "flat";
    var showChg = d && !indicative && chg !== 0;
    var priceTxt = d ? fmtPrice(d.display) : "—";
    var title = indicative
      ? "Indicative price (bid/ask mid) — no trades yet. Trade ANM/USDT on NonKYC."
      : "ANM/USDT on NonKYC — click to trade";
    el._root.innerHTML =
      "<style>" + CSS + "</style>" +
      '<a class="w' + (indicative ? " ind" : "") + '" href="' + market +
      '" target="_blank" rel="noopener" title="' + title + '" aria-label="Trade ANM on NonKYC">' +
      '<span class="dot"></span>' +
      '<span class="sym">ANM</span>' +
      '<span class="px">' + priceTxt + "</span>" +
      (showChg ? '<span class="chg ' + chgCls + '">' + fmtChg(chg) + "</span>" : "") +
      '<span class="tag">USDT · NonKYC</span>' +
      "</a>";
  }

  function load(el) {
    var ep = el.getAttribute("endpoint") || "/anm-price.json";
    fetch(ep, { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) render(el, d); })
      .catch(function () { /* keep last render */ });
  }

  proto.connectedCallback = function () {
    if (!this._root) {
      this._root = this.attachShadow ? this.attachShadow({ mode: "open" }) : this;
      render(this, null);
    }
    load(this);
    var self = this;
    this._timer = setInterval(function () { load(self); }, 60000);
  };
  proto.disconnectedCallback = function () { clearInterval(this._timer); };

  customElements.define(
    "anm-price-ticker",
    (function () {
      var C = function () { return Reflect.construct(HTMLElement, [], C); };
      C.prototype = proto;
      Object.setPrototypeOf(C, HTMLElement);
      return C;
    })()
  );
})();
