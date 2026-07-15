"use client";

import { useEffect, useState } from "react";
import { Search, Globe, Puzzle, Rocket, ArrowRight, ShieldCheck } from "lucide-react";

// The portal to the Animica Internet, featured at the top of animica.net. It reads the LIVE .anm
// index from the public, CORS-open gateway API (animica.dev). No key, no auth — just discovery.

const GW = "https://animica.dev";
const API = GW + "/api/mkt/v1/names";

type AnmSite = { name: string; kind?: string; contentCid?: string | null; recordsJson?: string };

function rec(s: AnmSite): any { try { return JSON.parse(s.recordsJson || "{}") || {}; } catch { return {}; } }
function avatarOf(s: AnmSite): string { return rec(s).avatar || (s.kind === "agent" ? "🤖" : s.kind === "ai" ? "✨" : "🌐"); }
function descOf(s: AnmSite): string { return rec(s).description || `A ${s.kind || "site"} on the Animica Internet.`; }

export function AnmInternetPortal() {
  const [q, setQ] = useState("");
  const [all, setAll] = useState<AnmSite[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    fetch(`${API}?search=`)
      .then((r) => r.json())
      .then((d) => {
        setAll((d.results || []).slice().sort((a: AnmSite, b: AnmSite) => a.name.localeCompare(b.name)));
        setTotal(typeof d.total === "number" ? d.total : (d.results || []).length);
      })
      .catch(() => setErr(true));
  }, []);

  const query = q.trim().toLowerCase();
  const shown = query
    ? all.filter((s) => s.name.includes(query) || descOf(s).toLowerCase().includes(query))
    : all;

  function open(name: string) { window.open(`${GW}/anm/${encodeURIComponent(name)}`, "_blank", "noopener"); }
  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const v = query.replace(/\.anm$/, "");
    if (/^[a-z0-9-]{2,63}$/.test(v)) open(v);
    else if (v) window.open(`${GW}/portal`, "_blank", "noopener");
  }

  return (
    <section className="relative overflow-hidden border-b border-white/5">
      <div className="pointer-events-none absolute inset-0 bg-nodes opacity-50" />
      <div className="container-x relative pb-14 pt-20 sm:pt-24">
        <div className="mx-auto max-w-3xl text-center">
          <span className="chip mx-auto border-cyan/30 bg-cyan/10 text-cyan-400">
            <Globe className="h-3.5 w-3.5" /> The Animica Internet is live{total != null ? ` · ${total} sites` : ""}
          </span>
          <h1 className="mt-5 text-4xl font-bold leading-[1.05] tracking-tight text-white sm:text-5xl">
            The sovereign{" "}
            <span className="bg-gradient-to-r from-cyan-400 to-neon-purple bg-clip-text text-transparent">.anm</span>{" "}
            internet
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-slate-300 sm:text-lg">
            Search every name, app, agent and AI service on the Animica network — owned by wallets, resolved by
            nodes, no registrar. Then get the browser and deploy your own.
          </p>

          {/* live search */}
          <form onSubmit={onSubmit} className="glass mx-auto mt-7 flex max-w-xl items-center gap-2 rounded-full py-1.5 pl-4 pr-1.5">
            <Search className="h-5 w-5 shrink-0 text-slate-400" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search the .anm web…  try: market, agents, media"
              aria-label="Search .anm names"
              className="min-w-0 flex-1 bg-transparent py-2 text-white placeholder:text-slate-500 focus:outline-none"
            />
            <button type="submit" className="btn-primary rounded-full !px-5 !py-2 text-sm">Open</button>
          </form>

          <div className="mt-4 flex flex-wrap justify-center gap-3">
            <a href={`${GW}/anm/animica`} target="_blank" rel="noopener" className="btn-ghost !py-2 text-sm">
              animica.anm <ArrowRight className="h-4 w-4" />
            </a>
            <a href={`${GW}/portal#deploy`} target="_blank" rel="noopener" className="btn-ghost !py-2 text-sm">
              Deploy your own <Rocket className="h-4 w-4" />
            </a>
            <a href={`${GW}/browser`} target="_blank" rel="noopener" className="btn-ghost !py-2 text-sm">
              Get the extension <Puzzle className="h-4 w-4" />
            </a>
          </div>
        </div>

        {/* directory */}
        <div className="relative mx-auto mt-12 max-w-5xl">
          {err ? (
            <p className="text-center text-sm text-slate-400">Could not reach the Animica Internet index right now.</p>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {shown.slice(0, 12).map((s) => (
                <button
                  key={s.name}
                  onClick={() => open(s.name)}
                  className="glass group flex flex-col items-start gap-1.5 p-3.5 text-left transition hover:border-iris/40"
                >
                  <div className="flex w-full items-center gap-2">
                    <span className="text-lg">{avatarOf(s)}</span>
                    <span className="truncate font-mono text-sm text-cyan-400">{s.name}.anm</span>
                    {s.contentCid ? (
                      <span className="ml-auto rounded-full border border-emerald/30 bg-emerald/10 px-1.5 py-0.5 text-[10px] text-emerald">
                        native
                      </span>
                    ) : null}
                  </div>
                  <p className="line-clamp-2 text-xs text-slate-400">{descOf(s)}</p>
                </button>
              ))}
              {shown.length === 0 && total != null && (
                <p className="col-span-full text-center text-sm text-slate-400">
                  No .anm site matches “{q}”. <a className="text-cyan-400" href={`${GW}/portal`} target="_blank" rel="noopener">Register it →</a>
                </p>
              )}
            </div>
          )}
        </div>

        {/* three pillars */}
        <div className="mx-auto mt-10 grid max-w-5xl gap-3 sm:grid-cols-3">
          <a href={`${GW}/browser`} target="_blank" rel="noopener" className="glass p-4 transition hover:border-cyan/40">
            <Puzzle className="h-5 w-5 text-cyan-400" />
            <h3 className="mt-2 font-semibold text-white">The browser</h3>
            <p className="mt-1 text-sm text-slate-400">Type <span className="font-mono text-cyan-400">name.anm</span> in your address bar, or use the zero-install gateway.</p>
          </a>
          <a href={`${GW}/portal#deploy`} target="_blank" rel="noopener" className="glass p-4 transition hover:border-iris/40">
            <Rocket className="h-5 w-5 text-iris-400" />
            <h3 className="mt-2 font-semibold text-white">Deploy a site</h3>
            <p className="mt-1 text-sm text-slate-400">Publish one self-contained HTML file the network serves from its content hash.</p>
          </a>
          <a href={`${GW}/anm/vpn`} target="_blank" rel="noopener" className="glass p-4 transition hover:border-emerald/40">
            <ShieldCheck className="h-5 w-5 text-emerald" />
            <h3 className="mt-2 font-semibold text-white">Browse privately</h3>
            <p className="mt-1 text-sm text-slate-400">Route your traffic through a dVPN exit you choose — relays earn ANM.</p>
          </a>
        </div>
      </div>
    </section>
  );
}
