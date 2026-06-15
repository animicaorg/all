/* ENA site — live progress stats + UX. Zero dependencies. */
(function () {
  "use strict";

  // Sample baseline shown when the coordinator is offline (clearly labelled).
  var SAMPLE = {
    jobs_total: 0, jobs_verified: 0, training_runs_total: 0, contributors: 0,
    distinct_models: 0, datasets_total: 0, chunks_total: 0, receipts_total: 0,
    leaderboard: [], recent_jobs: [], _offline: true
  };

  function fmt(n) {
    if (n == null || isNaN(n)) return "0";
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
    return String(n);
  }

  function animateTo(el, target) {
    var start = 0, t0 = null, dur = 900;
    function step(ts) {
      if (!t0) t0 = ts;
      var p = Math.min(1, (ts - t0) / dur);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = fmt(Math.round(start + (target - start) * eased));
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function render(stats) {
    document.querySelectorAll("[data-stat]").forEach(function (el) {
      var key = el.getAttribute("data-stat");
      var val = Number(stats[key] || 0);
      animateTo(el, val);
    });

    var lb = document.getElementById("lb");
    var rows = stats.leaderboard || [];
    if (lb) {
      lb.innerHTML = rows.length
        ? rows.map(function (r) {
            return "<tr><td class='mono'>" + esc(r.worker_id) + "</td><td>" + fmt(r.jobs) + "</td></tr>";
          }).join("")
        : "<tr><td class='mono'>be the first…</td><td>0</td></tr>";
    }

    var rec = document.getElementById("recent");
    var jobs = stats.recent_jobs || [];
    if (rec) {
      rec.innerHTML = jobs.length
        ? jobs.map(function (j) {
            return "<tr><td class='mono'>" + esc((j.job_id || "").slice(0, 14)) +
              "…</td><td>" + esc(j.job_type) + "</td><td>" + statusPill(j.status) + "</td></tr>";
          }).join("")
        : "<tr><td class='mono'>no jobs yet</td><td>—</td><td>—</td></tr>";
    }

    var badge = document.getElementById("liveBadge");
    var txt = document.getElementById("liveText");
    if (badge && txt) {
      if (stats._offline) { badge.classList.add("off"); txt.textContent = "coordinator offline · sample data"; }
      else { badge.classList.remove("off"); txt.textContent = "live · updated just now"; }
    }
  }

  function statusPill(s) {
    var color = s === "verified" || s === "completed" ? "var(--ok)"
      : s === "rejected" ? "#f06a6a" : "var(--muted)";
    return "<span style='color:" + color + "'>" + esc(s || "—") + "</span>";
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function loadStats() {
    var ctrl = new AbortController();
    var to = setTimeout(function () { ctrl.abort(); }, 4000);
    fetch("/api/stats", { signal: ctrl.signal, headers: { accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) { clearTimeout(to); render(data); })
      .catch(function () { clearTimeout(to); render(SAMPLE); });
  }

  // copy buttons on every <pre>
  function wireCopy() {
    document.querySelectorAll("pre").forEach(function (pre) {
      var btn = document.createElement("button");
      btn.className = "cp"; btn.type = "button"; btn.textContent = "copy";
      btn.addEventListener("click", function () {
        var code = pre.querySelector("code");
        var text = code ? code.textContent : pre.textContent;
        navigator.clipboard.writeText(text).then(function () {
          btn.textContent = "copied!";
          setTimeout(function () { btn.textContent = "copy"; }, 1400);
        }).catch(function () { btn.textContent = "select+copy"; });
      });
      pre.appendChild(btn);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var yr = document.getElementById("yr");
    if (yr) yr.textContent = new Date().getFullYear();
    wireCopy();
    loadStats();
    setInterval(loadStats, 30000); // refresh every 30s
  });
})();

/* ---- Demand: wallet-funded job requests ---------------------------------- */
(function () {
  "use strict";
  var cfg = null, account = null;
  var $ = function (id) { return document.getElementById(id); };

  function log(msg, cls) {
    var ol = $("reqLog");
    if (!ol) return;
    var li = document.createElement("li");
    if (cls) li.className = cls;
    li.innerHTML = "<b>" + msg + "</b>";
    ol.insertBefore(li, ol.firstChild);
  }
  function setStatus(t) { var el = $("reqStatus"); if (el) el.textContent = t; }

  function paramsFor(jobType, val) {
    if (jobType === "scrape") return { url: val };
    if (["dataset_clean", "embed", "training_records", "train_prepare", "eval", "label"].indexOf(jobType) >= 0)
      return { dataset: val };
    return { source: val }; // extract, chunk, index, summarize
  }

  function api(path, body) {
    return fetch("/api" + path, {
      method: body ? "POST" : "GET",
      headers: { "content-type": "application/json" },
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); });
  }

  function loadConfig() {
    if (!$("reqForm")) return;
    api("/demand/config").then(function (res) {
      cfg = res.j;
      if (!cfg.enabled) {
        $("reqForm").style.display = "none";
        $("reqDisabled").style.display = "block";
        return;
      }
      var sel = $("jobType");
      sel.innerHTML = cfg.job_types.map(function (t) { return "<option>" + t + "</option>"; }).join("");
      $("treasury").textContent = cfg.treasury_address;
      $("minHint").textContent = "(min " + cfg.min_anm + ")";
      $("reward").min = cfg.min_anm;
    }).catch(function () {
      if ($("reqDisabled")) { $("reqForm").style.display = "none"; $("reqDisabled").style.display = "block"; }
    });
  }

  // The extension injects window.animica asynchronously and fires
  // 'animica#initialized' when ready — wait for it rather than assuming.
  function getProvider(timeoutMs) {
    return new Promise(function (resolve) {
      if (window.animica && window.animica.request) return resolve(window.animica);
      var done = false;
      function settle() {
        if (done) return; done = true;
        resolve(window.animica && window.animica.request ? window.animica : null);
      }
      window.addEventListener("animica#initialized", settle, { once: true });
      window.addEventListener("ethereum#initialized", settle, { once: true });
      setTimeout(settle, timeoutMs || 1800);
    });
  }

  function noWallet() {
    var a = $("acct");
    if (a) a.innerHTML = 'No Animica wallet detected — <a class="grad" href="https://animica.org/wallet" target="_blank" rel="noopener">install it</a>, then reload.';
    log("Animica wallet extension not found.", "err");
  }

  function esc2(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function setAccount(addr) {
    account = addr || null;
    var btn = $("connectBtn"), acct = $("acct");
    if (account) {
      if (acct) acct.textContent = account.slice(0, 10) + "…" + account.slice(-6);
      if (btn) { btn.textContent = "✓ Connected"; btn.classList.add("btn-ghost"); btn.classList.remove("btn-primary"); }
      log("Wallet connected: " + account.slice(0, 12) + "…", "ok");
      closeModal();
    }
  }

  function openModal() { var m = $("wcModal"); if (m) { m.style.display = "flex"; wcPanel(""); } }
  function closeModal() { var m = $("wcModal"); if (m) m.style.display = "none"; }
  function wcPanel(html) { var p = $("wcPanel"); if (p) p.innerHTML = html; }

  // 1) Browser extension — triggers the extension's own approval popup.
  function connectExtension() {
    wcPanel("<p class='note'>Opening the extension — approve the connection there.</p>");
    return getProvider(1800).then(function (prov) {
      if (!prov) {
        wcPanel("<p class='note'>No Animica extension detected. <a class='grad' href='https://animica.org/wallet' target='_blank' rel='noopener'>Install it</a> and reload, or use the web/mobile option.</p>");
        return;
      }
      return prov.request({ method: "animica_requestAccounts" }).then(function (accts) {
        var a = (accts && accts[0]) || null;
        if (a) setAccount(a); else wcPanel("<p class='note'>No account was returned.</p>");
      }).catch(function (e) {
        wcPanel("<p class='note'>Connection " + (e && e.message ? esc2(e.message) : "rejected") + ".</p>");
      });
    });
  }

  function startWC() {
    return api("/wallet/start", { appOrigin: location.origin }).then(function (res) {
      if (!res.ok) throw new Error(res.j && res.j.error ? res.j.error : "could not start");
      return res.j; // {requestId, popupUrl, expiresAt}
    });
  }

  function pollWC(requestId) {
    var tries = 0;
    var iv = setInterval(function () {
      tries++;
      fetch("/api/wallet/poll?id=" + encodeURIComponent(requestId))
        .then(function (r) { return r.json(); }).then(function (j) {
          if (j.status === "approved" && j.address) { clearInterval(iv); setAccount(j.address); }
          else if (j.status === "rejected") { clearInterval(iv); wcPanel("<p class='note'>Connection was rejected.</p>"); }
          else if (j.status === "expired" || tries > 90) { clearInterval(iv); wcPanel("<p class='note'>Request expired — please try again.</p>"); }
        }).catch(function () {});
    }, 2000);
  }

  // 2) Web wallet — opens wallet.animica.org connect popup; user approves there.
  function connectWeb() {
    wcPanel("<p class='note'>Opening wallet.animica.org…</p>");
    startWC().then(function (s) {
      window.open(s.popupUrl, "animica-wallet", "width=460,height=760");
      wcPanel("<p class='note'>Approve the connection in the popup window.<br>" +
        "<a class='grad wclink' href='" + s.popupUrl + "' target='_blank' rel='noopener'>Popup blocked? Open it here</a></p>");
      pollWC(s.requestId);
    }).catch(function (e) { wcPanel("<p class='note'>Error: " + esc2(e.message) + "</p>"); });
  }

  // 3) Mobile wallet — QR + deep link to the same approve page on a phone.
  function connectMobile() {
    wcPanel("<p class='note'>Preparing a secure link…</p>");
    startWC().then(function (s) {
      var qr = "https://api.qrserver.com/v1/create-qr-code/?size=190x190&data=" + encodeURIComponent(s.popupUrl);
      wcPanel("<p class='note'>Scan with your phone's camera, or open on this device:</p>" +
        "<img alt='Connect QR' width='180' height='180' src='" + qr + "'/>" +
        "<p><a class='btn btn-primary' style='display:inline-block;text-decoration:none' href='" + s.popupUrl + "' target='_blank' rel='noopener'>Open wallet</a></p>");
      pollWC(s.requestId);
    }).catch(function (e) { wcPanel("<p class='note'>Error: " + esc2(e.message) + "</p>"); });
  }

  function wcDispatch(kind) {
    if (kind === "extension") return connectExtension();
    if (kind === "web") return connectWeb();
    if (kind === "mobile") return connectMobile();
  }

  function poll(jobId, txid, tries) {
    tries = tries || 0;
    return api("/demand/confirm", { job_id: jobId, txid: txid }).then(function (res) {
      var j = res.j;
      if (j.funded) {
        log("Funded ✓ — job is now in the queue (reward " + (j.reward_anm || "") + " ANM)", "ok");
        setStatus("Funded. Workers can now claim job " + jobId.slice(0, 14) + "…");
        return j;
      }
      if (j.pending && tries < 30) {
        setStatus("Payment seen, awaiting confirmation… (" + (j.tx_status || "pending") + ")");
        return new Promise(function (r) { setTimeout(r, 5000); }).then(function () {
          return poll(jobId, txid, tries + 1);
        });
      }
      log("Not funded: " + (j.reason || "unknown") + (j.tx_status ? " [" + j.tx_status + "]" : ""), "err");
      setStatus("Could not fund the job: " + (j.reason || "unknown"));
      return j;
    });
  }

  function submit() {
    if (!cfg || !cfg.enabled) return;
    var jobType = $("jobType").value;
    var src = ($("jobSource").value || "").trim();
    var reward = parseFloat($("reward").value);
    if (!src) { log("Enter a source / dataset / URL first", "err"); return; }
    if (!(reward >= cfg.min_anm)) { log("Reward must be ≥ " + cfg.min_anm + " ANM", "err"); return; }

    if (!account) { openModal(); log("Connect a wallet to fund a job.", ""); return; }
    var acct = account;
    Promise.resolve().then(function () {
      setStatus("Requesting a quote…");
      return api("/demand/quote", {
        job_type: jobType, params: paramsFor(jobType, src), reward_anm: reward, requester: acct
      }).then(function (res) {
        if (!res.ok) { log("Quote failed: " + (res.j.error || res.j.reason || "error"), "err"); return; }
        var q = res.j;
        log("Quote: " + q.required_anm + " ANM for " + jobType + " (job " + q.job_id.slice(0, 12) + "…)");
        setStatus("Approve the payment in your wallet…");
        return getProvider(1800).then(function (prov) {
          if (!prov) { noWallet(); throw new Error("no wallet"); }
          return prov.request({
            method: "animica_sendTransaction",
            params: [{ from: acct, to: q.treasury_address, value: String(q.required_nano), memo: q.memo }]
          });
        }).then(function (txid) {
          log("Payment sent: " + String(txid).slice(0, 18) + "…", "ok");
          setStatus("Verifying payment on-chain…");
          return poll(q.job_id, txid);
        });
      });
    }).catch(function (e) {
      log("Error: " + (e && e.message ? e.message : e), "err");
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!$("reqForm")) return;
    loadConfig();
    var c = $("connectBtn"); if (c) c.addEventListener("click", openModal);
    var s = $("submitBtn"); if (s) s.addEventListener("click", submit);
    var x = $("wcClose"); if (x) x.addEventListener("click", closeModal);
    var modal = $("wcModal");
    if (modal) modal.addEventListener("click", function (e) { if (e.target === modal) closeModal(); });
    var opts = document.querySelectorAll(".wc-opt");
    for (var i = 0; i < opts.length; i++) {
      (function (b) { b.addEventListener("click", function () { wcDispatch(b.getAttribute("data-wc")); }); })(opts[i]);
    }
  });
})();

/* ---- Training data: contribute + list ------------------------------------ */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function loadDatasets() {
    if (!$("dsList")) return;
    fetch("/api/datasets").then(function (r) { return r.json(); }).then(function (d) {
      var rows = d.datasets || [];
      if ($("dsCount")) $("dsCount").textContent = rows.length;
      $("dsList").innerHTML = rows.length
        ? rows.slice(0, 12).map(function (r) {
            return "<tr><td class='mono'>" + esc((r.name || r.dataset_id).slice(0, 20)) +
              "</td><td>" + esc(r.kind) + "</td><td>" + (r.row_count || 0) + "</td></tr>";
          }).join("")
        : "<tr><td class='mono'>none yet — be the first</td><td>—</td><td>—</td></tr>";
    }).catch(function () {});
  }

  function contribute() {
    var msg = $("dataMsg");
    var url = ($("dataUrl").value || "").trim();
    var text = ($("dataRows").value || "").trim();
    var body = null;
    if (text) {
      var rows = [];
      var bad = 0;
      text.split("\n").forEach(function (ln) {
        ln = ln.trim(); if (!ln) return;
        try { rows.push(JSON.parse(ln)); } catch (e) { bad++; }
      });
      if (!rows.length) { msg.textContent = "No valid JSONL rows found."; return; }
      if (bad) msg.textContent = "Note: skipped " + bad + " unparseable line(s).";
      body = { rows: rows, kind: "contributed" };
    } else if (url) {
      body = { url: url };
    } else {
      msg.textContent = "Paste JSONL rows or enter a URL first."; return;
    }
    msg.textContent = "Submitting…";
    fetch("/api/datasets/contribute", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body)
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.error) { msg.textContent = "Error: " + j.error; return; }
      if (j.mode === "url") {
        msg.innerHTML = "✓ Queued as scrape job <span class='kbd'>" + esc((j.job_id || "").slice(0, 14)) + "…</span>";
      } else {
        msg.innerHTML = "✓ Contributed " + (j.row_count || 0) + " rows (deduped). Thank you!";
        $("dataRows").value = "";
      }
      loadDatasets();
    }).catch(function (e) { msg.textContent = "Error: " + (e && e.message ? e.message : e); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!$("dsList")) return;
    loadDatasets();
    var b = $("dataBtn"); if (b) b.addEventListener("click", contribute);
  });
})();

/* ---- Training pools: pick, fund, watch ----------------------------------- */
(function () {
  "use strict";
  var account = null, treasury = null;
  var $ = function (id) { return document.getElementById(id); };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function msg(t, isErr) {
    var el = $("poolMsg");
    if (!el) return;
    el.textContent = t || "";
    el.style.color = isErr ? "#f06a6a" : "";
  }

  function api(path, body) {
    return fetch("/api" + path, {
      method: body ? "POST" : "GET",
      headers: { "content-type": "application/json" },
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); });
  }

  // The extension injects window.animica asynchronously and fires
  // 'animica#initialized' when ready — wait for it rather than assuming.
  function getProvider(timeoutMs) {
    return new Promise(function (resolve) {
      if (window.animica && window.animica.request) return resolve(window.animica);
      var done = false;
      function settle() {
        if (done) return; done = true;
        resolve(window.animica && window.animica.request ? window.animica : null);
      }
      window.addEventListener("animica#initialized", settle, { once: true });
      window.addEventListener("ethereum#initialized", settle, { once: true });
      setTimeout(settle, timeoutMs || 1800);
    });
  }

  function openModal() { var m = $("wcModal"); if (m) m.style.display = "flex"; }
  function closeModal() { var m = $("wcModal"); if (m) m.style.display = "none"; }

  function setAccount(addr) {
    account = addr || null;
    var btn = $("poolConnectBtn"), acct = $("poolAcct");
    if (account) {
      if (acct) acct.textContent = account.slice(0, 10) + "…" + account.slice(-6);
      if (btn) { btn.textContent = "✓ Connected"; btn.classList.add("btn-ghost"); btn.classList.remove("btn-primary"); }
      closeModal();
    }
  }

  function connect() {
    return getProvider(1800).then(function (prov) {
      if (!prov) { openModal(); return; }
      return prov.request({ method: "animica_requestAccounts" }).then(function (accts) {
        var a = (accts && accts[0]) || null;
        if (a) setAccount(a); else openModal();
      }).catch(function () { openModal(); });
    });
  }

  function loadPools() {
    if (!$("poolSelect")) return;
    api("/pool/list").then(function (res) {
      var pools = (res.j && res.j.pools) || [];
      if (!pools.length) {
        if ($("poolForm")) $("poolForm").style.display = "none";
        if ($("poolDisabled")) $("poolDisabled").style.display = "block";
        return;
      }
      var sel = $("poolSelect");
      if (!sel) return;
      sel.innerHTML = pools.map(function (p) {
        var label = p.name || p.pool_id;
        return "<option value='" + esc(p.pool_id) + "'>" + esc(label) + "</option>";
      }).join("");
      loadStatus();
    }).catch(function () {
      if ($("poolForm")) $("poolForm").style.display = "none";
      if ($("poolDisabled")) $("poolDisabled").style.display = "block";
    });
  }

  function statusRow(k, v) {
    return "<tr><td class='mono'>" + esc(k) + "</td><td>" + esc(v) + "</td></tr>";
  }

  // Render a {key: count} map as "k: v, k2: v2" (raw — statusRow escapes it).
  function fmtMap(m) {
    if (!m || typeof m !== "object") return "";
    var ks = Object.keys(m);
    if (!ks.length) return "";
    return ks.map(function (k) { return k + ": " + m[k]; }).join(", ");
  }

  function loadStatus() {
    var sel = $("poolSelect");
    if (!sel || !sel.value) return;
    var pid = sel.value;
    api("/pool/status?pool_id=" + encodeURIComponent(pid)).then(function (res) {
      var s = res.j || {};
      var tb = $("poolStatus");
      if (!tb) return;
      if (!res.ok) {
        tb.innerHTML = statusRow("error", (s.error || s.reason || "could not load"));
        return;
      }
      var rows = [];
      rows.push(statusRow("pool_id", s.pool_id || pid));
      if (s.name != null) rows.push(statusRow("name", s.name));
      if (s.base_model != null) rows.push(statusRow("base_model", s.base_model));
      if (s.method != null) rows.push(statusRow("method", s.method));
      if (s.status != null) rows.push(statusRow("status", s.status));
      if (s.round != null) rows.push(statusRow("round", s.round));
      if (s.num_shards != null) rows.push(statusRow("shards (total)", s.num_shards));
      if (s.shards != null && fmtMap(s.shards)) rows.push(statusRow("shards", fmtMap(s.shards)));
      if (s.contributions != null && fmtMap(s.contributions)) rows.push(statusRow("contributions", fmtMap(s.contributions)));
      if (s.budget_anm != null) rows.push(statusRow("budget (ANM)", s.budget_anm));
      if (s.paid_out_nano != null) rows.push(statusRow("paid out (nano)", s.paid_out_nano));
      if (s.reward_split != null && fmtMap(s.reward_split)) rows.push(statusRow("reward split (bps)", fmtMap(s.reward_split)));
      if (s.served_checkpoint) rows.push(statusRow("served checkpoint",
        "round " + s.served_checkpoint.round + " · " +
        String(s.served_checkpoint.checkpoint_hash || "").slice(0, 12) + "…"));
      tb.innerHTML = rows.join("");
    }).catch(function () {
      var tb = $("poolStatus");
      if (tb) tb.innerHTML = statusRow("error", "could not load");
    });
  }

  function poll(pid, txid, tries) {
    tries = tries || 0;
    return api("/pool/fund/confirm", { pool_id: pid, txid: txid }).then(function (res) {
      var j = res.j || {};
      if (j.funded) {
        msg("Funded ✓ — pool is now bankrolled.");
        loadStatus();
        return j;
      }
      if (tries < 30) {
        msg("Payment seen, awaiting confirmation… (" + (j.tx_status || "pending") + ")");
        return new Promise(function (r) { setTimeout(r, 5000); }).then(function () {
          return poll(pid, txid, tries + 1);
        });
      }
      msg("Not funded: " + (j.reason || "unknown"), true);
      return j;
    });
  }

  function fund() {
    var sel = $("poolSelect");
    if (!sel || !sel.value) { msg("Pick a pool first.", true); return; }
    var pid = sel.value;
    var amount = parseFloat(($("poolAmount") && $("poolAmount").value) || "");
    if (!(amount > 0)) { msg("Enter a reward amount greater than 0.", true); return; }
    if (!account) { openModal(); msg("Connect a wallet to fund a pool."); return; }
    var acct = account;
    Promise.resolve().then(function () {
      msg("Requesting a quote…");
      return api("/pool/fund/quote", { pool_id: pid, reward_anm: amount, requester: acct }).then(function (res) {
        if (!res.ok) { msg("Quote failed: " + (res.j.error || res.j.reason || "error"), true); return; }
        var q = res.j;
        treasury = q.treasury_address || treasury;
        if ($("poolTreasury") && treasury) $("poolTreasury").textContent = treasury;
        msg("Approve the payment in your wallet…");
        return getProvider(1800).then(function (prov) {
          if (!prov) { openModal(); throw new Error("no wallet"); }
          return prov.request({
            method: "animica_sendTransaction",
            params: [{ from: acct, to: q.treasury_address, value: String(q.required_nano), memo: q.memo }]
          });
        }).then(function (txid) {
          msg("Payment sent: " + String(txid).slice(0, 18) + "… — verifying…");
          return poll(pid, txid);
        });
      });
    }).catch(function (e) {
      msg("Error: " + (e && e.message ? e.message : e), true);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!$("poolSelect")) return;
    loadPools();
    var sel = $("poolSelect"); if (sel) sel.addEventListener("change", loadStatus);
    var c = $("poolConnectBtn"); if (c) c.addEventListener("click", connect);
    var f = $("poolFundBtn"); if (f) f.addEventListener("click", fund);
  });
})();
