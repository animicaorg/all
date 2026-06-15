"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

// The app is served under basePath "/app", but client-side fetch() does not get
// the basePath prepended automatically. So we hit the route with the explicit
// /app prefix, matching the rest of the app (see training-pools/page.tsx).
const API = "/app/api/downloads";

type Platform = "windows" | "macos" | "linux";

interface MinerDownload {
  platform: string;
  name: string;
  version: string;
  download_url: string;
  size_bytes: number;
  checksum_sha256: string;
  released_at: string | null;
}

interface DownloadsManifest {
  version: string;
  updated_at?: string;
  notes?: string;
  miners: MinerDownload[];
}

const PLATFORM_LABEL: Record<Platform, string> = {
  windows: "Windows",
  macos: "macOS",
  linux: "Linux",
};

const PLATFORM_HINT: Record<Platform, string> = {
  windows: "Windows 10/11 · x64 · unzip and run the .exe",
  macos: "macOS 11+ · Apple Silicon · open the .dmg",
  linux: "Linux x86_64 · extract the tarball and run",
};

// Order the cards Windows → macOS → Linux regardless of manifest order.
const PLATFORM_ORDER: Platform[] = ["windows", "macos", "linux"];

/** Best-effort OS detection from the UA string, to highlight the right build. */
function detectPlatform(ua: string): Platform | null {
  const s = ua.toLowerCase();
  if (s.includes("win")) return "windows";
  // "mac" matches macOS; iPhone/iPad report "like mac" but also "mobile".
  if (s.includes("mac") && !s.includes("mobile")) return "macos";
  if (s.includes("linux") || s.includes("x11")) return "linux";
  return null;
}

function formatSize(bytes: number): string {
  if (!bytes || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatChecksum(sha: string): string {
  if (!sha || sha === "TBD") return "checksum pending";
  return sha;
}

export default function DownloadPage() {
  const [manifest, setManifest] = useState<DownloadsManifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [detected, setDetected] = useState<Platform | null>(null);

  useEffect(() => {
    if (typeof navigator !== "undefined") {
      setDetected(detectPlatform(navigator.userAgent));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(API, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: DownloadsManifest = await res.json();
        if (!cancelled) setManifest(data);
      } catch {
        if (!cancelled) setError("Could not load the downloads list. Please try again.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Index miners by platform so we can render in a stable order and find the
  // detected build quickly.
  const byPlatform = useMemo(() => {
    const map = new Map<string, MinerDownload>();
    for (const m of manifest?.miners ?? []) map.set(m.platform, m);
    return map;
  }, [manifest]);

  const detectedMiner = detected ? byPlatform.get(detected) : undefined;

  return (
    <div className="space-y-12">
      {/* Hero */}
      <section className="space-y-4">
        <p className="text-sm font-medium uppercase tracking-wide text-blue-400">
          Desktop miners
        </p>
        <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">
          Download the Animica GUI miner.
        </h1>
        <p className="max-w-2xl text-lg text-white/70">
          A standalone desktop app for Windows, macOS, and Linux — no Python, no
          terminal. It runs the exact same{" "}
          <span className="text-white/90">unified flow</span> as the CLI: it mines
          the chain and contributes to the open{" "}
          <Link href="/about-ena" className="text-blue-400 hover:text-blue-300">
            ENA model
          </Link>{" "}
          (training + serving), and every reward is paid in{" "}
          <span className="text-white/90">ANM only</span>.
        </p>
      </section>

      {/* Detected build highlight */}
      {detectedMiner && (
        <section className="rounded-xl border border-blue-500/40 bg-blue-500/10 p-6">
          <p className="text-sm font-medium uppercase tracking-wide text-blue-300">
            Recommended for your system
          </p>
          <div className="mt-2 flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div>
              <h2 className="text-xl font-semibold">
                {PLATFORM_LABEL[detected as Platform]} · v{detectedMiner.version}
              </h2>
              <p className="mt-1 text-sm text-white/60">
                {PLATFORM_HINT[detected as Platform]}
              </p>
            </div>
            <a
              href={detectedMiner.download_url}
              className="inline-block rounded-lg bg-blue-600 px-5 py-2.5 font-medium text-white hover:bg-blue-500"
            >
              Download for {PLATFORM_LABEL[detected as Platform]} →
            </a>
          </div>
        </section>
      )}

      {/* All builds */}
      <section className="space-y-6">
        <div className="flex items-baseline justify-between">
          <h2 className="text-2xl font-semibold tracking-tight">All builds</h2>
          {manifest?.version && (
            <span className="text-sm text-white/40">
              release {manifest.version}
            </span>
          )}
        </div>

        {loading && (
          <p className="text-sm text-white/50">Loading downloads…</p>
        )}

        {error && !loading && (
          <p className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
            {error}
          </p>
        )}

        {!loading && !error && (
          <div className="grid gap-4 md:grid-cols-3">
            {PLATFORM_ORDER.map((platform) => {
              const miner = byPlatform.get(platform);
              const isDetected = detected === platform;
              return (
                <div
                  key={platform}
                  className={`flex flex-col rounded-xl border p-6 ${
                    isDetected
                      ? "border-blue-500/50 bg-blue-500/5"
                      : "border-white/10 bg-white/5"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <h3 className="text-lg font-medium">
                      {PLATFORM_LABEL[platform]}
                    </h3>
                    {isDetected && (
                      <span className="rounded-full bg-blue-500/20 px-2 py-0.5 text-xs font-medium text-blue-300">
                        your OS
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-white/40">
                    {PLATFORM_HINT[platform]}
                  </p>

                  {miner ? (
                    <>
                      <dl className="mt-4 space-y-2 text-sm">
                        <div className="flex justify-between gap-2">
                          <dt className="text-white/40">Version</dt>
                          <dd className="text-white/70">{miner.version}</dd>
                        </div>
                        <div className="flex justify-between gap-2">
                          <dt className="text-white/40">Size</dt>
                          <dd className="text-white/70">
                            {formatSize(miner.size_bytes)}
                          </dd>
                        </div>
                        {miner.released_at && (
                          <div className="flex justify-between gap-2">
                            <dt className="text-white/40">Released</dt>
                            <dd className="text-white/70">
                              {new Date(miner.released_at)
                                .toISOString()
                                .slice(0, 10)}
                            </dd>
                          </div>
                        )}
                      </dl>

                      <div className="mt-3">
                        <p className="text-xs text-white/40">SHA-256</p>
                        <p className="mt-1 break-all font-mono text-[11px] leading-snug text-white/50">
                          {formatChecksum(miner.checksum_sha256)}
                        </p>
                      </div>

                      <a
                        href={miner.download_url}
                        className={`mt-5 inline-block rounded-lg px-4 py-2 text-center font-medium ${
                          isDetected
                            ? "bg-blue-600 text-white hover:bg-blue-500"
                            : "border border-white/10 text-white/80 hover:border-blue-500/50 hover:text-white"
                        }`}
                      >
                        Download
                      </a>
                    </>
                  ) : (
                    <p className="mt-4 text-sm text-white/40">
                      Build not available yet.
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <p className="text-xs text-white/40">
          Always verify the SHA-256 checksum of the file you downloaded against
          the value shown here before running it.
        </p>
      </section>

      {/* CLI alternative */}
      <section className="rounded-xl border border-white/10 bg-white/5 p-6">
        <h2 className="text-xl font-medium">Prefer the CLI?</h2>
        <p className="mt-2 max-w-2xl text-sm text-white/60">
          The GUI is just a wrapper around the same unified miner. If you live in
          a terminal, skip the download entirely — one command installs and runs
          the whole stack (mining + ENA training and serving), all ANM-bound.
        </p>
        <pre className="mt-3 overflow-x-auto rounded-lg border border-white/10 bg-black/40 p-4 text-sm text-white/90">
          <code>{`pip install --upgrade animica && animica up`}</code>
        </pre>
        <p className="mt-3 text-xs text-white/40">
          Want hardware tips, pool selection, and running as a service? See the{" "}
          <Link
            href="/mining-onboard"
            className="text-blue-400 hover:text-blue-300"
          >
            mining onboarding
          </Link>{" "}
          guide.
        </p>
      </section>

      {/* What it runs */}
      <section className="rounded-xl border border-blue-500/30 bg-blue-500/5 p-6">
        <h2 className="text-xl font-medium">Same flow, friendlier face</h2>
        <p className="mt-2 max-w-2xl text-sm text-white/60">
          Whichever you pick, you are running the one unified Animica flow: the
          app secures the chain and points its compute at the open ENA training
          pools, serving inference between rounds. Rewards are paid in{" "}
          <span className="text-white/80">ANM only</span> to your Animica address.
        </p>
        <div className="mt-4 flex flex-wrap gap-3">
          <Link
            href="/about-ena"
            className="rounded-lg bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-500"
          >
            What is ENA?
          </Link>
          <Link
            href="/training-pools"
            className="rounded-lg border border-white/10 px-4 py-2 font-medium text-white/80 hover:border-blue-500/50 hover:text-white"
          >
            Browse training pools
          </Link>
        </div>
      </section>
    </div>
  );
}
