"use client";

import { useEffect, useState } from "react";

const RELEASES = "https://github.com/animicaorg/all/releases/latest";
const PLATFORMS = [
  { key: "windows", name: "Windows", note: "Windows 10/11 · .exe (zip)", match: /win/i },
  { key: "macos", name: "macOS", note: "macOS 12+ · .app (dmg)", match: /mac/i },
  { key: "linux", name: "Linux", note: "x86-64 · AppImage / tar.gz", match: /linux|x11/i },
];

export default function DownloadPage() {
  const [os, setOs] = useState<string>("");
  useEffect(() => {
    const ua = navigator.userAgent || "";
    const hit = PLATFORMS.find((p) => p.match.test(ua));
    setOs(hit?.key ?? "");
  }, []);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold">Download the <span className="text-neon-green">GUI miner</span></h1>
        <p className="mt-1 max-w-2xl text-white/60">
          A one-click desktop app (Qt) that runs the same unified flow as the CLI:
          mining + AI useful-work + training + serving (and Bittensor on qualified GPUs),
          all paid in ANM. Prefer the terminal? See the CLI below.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        {PLATFORMS.map((p) => (
          <a key={p.key} href={RELEASES}
             className={`rounded-xl border p-5 transition hover:border-neon-green/50 ${
               os === p.key ? "border-neon-green/60 bg-white/10" : "border-white/10 bg-white/5"}`}>
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">{p.name}</h3>
              {os === p.key && <span className="text-xs text-neon-green">detected</span>}
            </div>
            <p className="mt-1 text-sm text-white/50">{p.note}</p>
            <span className="btn-primary mt-4 inline-block">Download ↗</span>
          </a>
        ))}
      </div>
      <p className="text-sm text-white/50">
        Builds and SHA-256 checksums are published on the{" "}
        <a href={RELEASES} className="underline">GitHub release</a> (Windows/macOS/Linux,
        from the signed CI matrix).
      </p>

      <section className="rounded-xl border border-white/10 bg-white/5 p-5">
        <h2 className="text-lg font-semibold">Prefer the CLI?</h2>
        <p className="mt-1 text-sm text-white/60">One command does everything (auto-creates a wallet):</p>
        <pre className="mt-3 w-fit rounded-xl border border-white/10 bg-black/40 p-4 text-sm">
pip install --upgrade animica{"\n"}animica up</pre>
      </section>
    </div>
  );
}
