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
