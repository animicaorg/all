#!/usr/bin/env node
// Animica Pool CLI — as easy as `animica miner mine-blocks` with flags.
//   animica-pool mine --mode dual --address anim1… --xmr 4… --payout ANM --worker rig-01
import { parseArgs } from "node:util";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const CFG_DIR = join(homedir(), ".config", "animica-pool");
const CFG_FILE = join(CFG_DIR, "config.json");

function loadCfg() {
  try { return JSON.parse(readFileSync(CFG_FILE, "utf8")); } catch { return {}; }
}
function saveCfg(c) { mkdirSync(CFG_DIR, { recursive: true }); writeFileSync(CFG_FILE, JSON.stringify(c, null, 2)); }
function apiBase() { return process.env.ANIMICA_POOL_API || loadCfg().api || "https://pool.animica.org"; }

async function req(method, path, { body, auth = true, key } = {}) {
  const cfg = loadCfg();
  const headers = { "content-type": "application/json" };
  if (auth && cfg.cookie) headers.cookie = cfg.cookie;
  if (key) headers.authorization = `Bearer ${key}`;
  const res = await fetch(`${apiBase()}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const setCookie = res.headers.get("set-cookie");
  if (setCookie && /animica_pool_session=/.test(setCookie)) {
    cfg.cookie = setCookie.split(";")[0];
    saveCfg(cfg);
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(data?.error?.message || data?.message || `${res.status} ${path}`);
  return data;
}

const out = (o) => console.log(typeof o === "string" ? o : JSON.stringify(o, null, 2));
function flags(spec) {
  return parseArgs({ args: process.argv.slice(3), options: spec, allowPositionals: true }).values;
}

const commands = {
  async config() {
    const v = flags({ api: { type: "string" } });
    const c = loadCfg();
    if (v.api) { c.api = v.api; saveCfg(c); }
    out({ api: apiBase(), loggedIn: !!c.cookie });
  },
  async register() {
    const v = flags({ email: { type: "string" }, password: { type: "string" } });
    out(await req("POST", "/api/auth/register", { auth: false, body: { email: v.email, password: v.password } }));
  },
  async login() {
    const v = flags({ email: { type: "string" }, password: { type: "string" } });
    out(await req("POST", "/api/auth/login", { auth: false, body: { email: v.email, password: v.password } }));
    out("✓ logged in (session saved)");
  },
  async logout() { await req("POST", "/api/auth/logout"); const c = loadCfg(); delete c.cookie; saveCfg(c); out("✓ logged out"); },
  async whoami() { out(await req("GET", "/api/auth/me")); },

  async keys() {
    const sub = process.argv[3];
    if (sub === "create") { const v = flags({ label: { type: "string" } }); out(await req("POST", "/api/api-keys", { body: { label: v.label } })); }
    else out(await req("GET", "/api/api-keys"));
  },
  async credits() {
    const sub = process.argv[3];
    if (sub === "buy") { const v = flags({ amount: { type: "string" } }); const r = await req("POST", "/api/credits/checkout", { body: { amountUsd: Number(v.amount) } }); out(`Pay here: ${r.invoiceUrl}`); }
    else out(await req("GET", "/api/credits/balance"));
  },

  async mine() {
    const v = flags({ mode: { type: "string" }, address: { type: "string" }, xmr: { type: "string" }, payout: { type: "string" }, worker: { type: "string" }, username: { type: "string" } });
    const modeMap = { anm: "ANM_ONLY", xmr: "XMR_ONLY", dual: "DUAL_50_50" };
    const mode = modeMap[(v.mode || "").toLowerCase()] || v.mode || "ANM_ONLY";
    await req("POST", "/api/mining/account", { body: {
      username: v.username || v.address?.slice(0, 16) || "miner", workerName: v.worker || "rig-01",
      mode, payoutAsset: v.payout || "ANM", anmAddress: v.address, xmrAddress: v.xmr,
    }});
    const s = await req("GET", "/api/mining/stats");
    out(`✓ Mining account ready (${s.account.mode}, payout ${s.account.payoutAsset}).`);
    out(`\nConnect your miner:\n  Pool: ${s.connection.poolUrl}\n  ${s.connection.command}\n`);
    out(`Hashrate ${s.stats.hashrate} H/s · pending ${s.stats.pendingBalanceAnm} ANM · est $${s.estimatedEarningsUsd.toFixed(4)}`);
  },
  async "mine-stats"() { out(await req("GET", "/api/mining/stats")); },

  async worker() {
    const sub = process.argv[3];
    if (sub === "create") {
      const v = flags({ name: { type: "string" }, mode: { type: "string" } });
      const r = await req("POST", "/api/workers", { body: { name: v.name || "worker-1", earningMode: v.mode || "all" } });
      out(`✓ Worker created. Install it on your machine:\n  curl -fsSL ${apiBase()}/install-worker.sh | bash -s -- ${r.token}`);
    } else if (sub === "delete" || sub === "rm") {
      const id = process.argv[4];
      if (!id) { out("usage: animica-pool worker delete <id>"); return; }
      out(await req("DELETE", `/api/workers/${id}`));
    } else out(await req("GET", "/api/workers"));
  },

  async rent() {
    const sub = process.argv[3];
    if (sub === "list" || !sub || sub.startsWith("--")) {
      const v = flags({ type: { type: "string" } });
      out(await req("GET", `/api/rentals/listings${v.type ? `?providerType=${v.type}` : ""}`, { auth: false }));
    } else {
      const v = flags({ rig: { type: "string" }, hours: { type: "string" }, pay: { type: "string" } });
      out(await req("POST", "/api/rentals/orders", { body: { listingId: v.rig, hours: Number(v.hours || 1), payWith: v.pay || "credits" } }));
    }
  },

  async infer() {
    const v = flags({ key: { type: "string" }, model: { type: "string" }, prompt: { type: "string" } });
    const r = await req("POST", "/v1/chat/completions", {
      auth: false, key: v.key,
      body: { model: v.model || "anm-fast-8b", messages: [{ role: "user", content: v.prompt || "Hello" }] },
    });
    out(r.choices?.[0]?.message?.content ?? r);
  },

  async payout() {
    const sub = process.argv[3];
    if (sub === "request") {
      const v = flags({ type: { type: "string" }, asset: { type: "string" }, address: { type: "string" }, amount: { type: "string" } });
      out(await req("POST", "/api/payouts/requests", { body: { type: v.type || "worker_inference", asset: v.asset || "USDT", address: v.address, amountUsd: Number(v.amount) } }));
    } else out(await req("GET", "/api/payouts/balance"));
  },

  async providers() { out(await req("GET", "/api/providers", { auth: false })); },
  async revenue() { out(await req("GET", "/api/revenue/public", { auth: false })); },

  help() {
    out(`Animica Pool CLI — API: ${apiBase()}

  animica-pool config --api <url>
  animica-pool register --email <e> --password <p>
  animica-pool login --email <e> --password <p>
  animica-pool whoami | logout
  animica-pool keys [list] | keys create --label <l>
  animica-pool credits | credits buy --amount <usd>
  animica-pool mine --mode <anm|xmr|dual> --address anim1… [--xmr <addr>] [--payout ANM] [--worker rig-01]
  animica-pool mine-stats
  animica-pool worker [list] | worker create --name <n> --mode <all|mining|inference|rental> | worker delete <id>
  animica-pool rent [list] [--type gpu|cpu] | rent order --rig <id> --hours <n> --pay <credits|crypto>
  animica-pool infer --key anm_live_… --model anm-fast-8b --prompt "…"
  animica-pool payout [balance] | payout request --asset USDT --address <a> --amount <usd>
  animica-pool providers | revenue
`);
  },
};

const cmd = process.argv[2] || "help";
Promise.resolve((commands[cmd] || commands.help)()).catch((e) => { console.error("✖", e.message); process.exit(1); });
