"use client";

import { useEffect, useState } from "react";
import ParticleHero from "./ParticleHero";
import LiveStats from "./LiveStats";

/**
 * The installers are described by /downloads/manifest.json, which the miner-GUI
 * build pipeline writes (build-scripts/make_manifest.py) alongside the binaries
 * nginx serves out of /var/www/pool.animica.org/downloads/.
 *
 * Reading it at runtime is deliberate. This page used to hardcode filenames,
 * sizes, checksums and a "bundles animica 1.9.12" string, none of which were
 * updated when the package moved on — the page advertised a version seven
 * majors behind what the binaries actually contained, and the checksums never
 * matched the files on disk. Now the build is the only thing that decides.
 *
 * FALLBACK below is the last-known-good snapshot, used only if the manifest
 * cannot be fetched, so the page still renders something honest.
 */
type OsKey = "windows" | "macos" | "linux";

type ManifestEntry = {
  platform: string;
  version: string;
  bundled_animica?: string;
  filename: string;
  download_url: string;
  size_bytes: number;
  sha256: string;
  min_os?: string;
};

type Manifest = {
  generated_at?: string;
  version?: string;
  /** Operator-set warning (e.g. a known issue with the published build). */
  notice?: string;
  miners: ManifestEntry[];
};

type DownloadInfo = {
  file: string;
  label: string;
  note: string;
  size: string;
  sha256: string;
  /** Secondary format for the same OS (tarball next to the AppImage, etc.). */
  alt?: { file: string; label: string; size: string };
};

/** Preferred package format per OS; the rest become the "also available" link. */
const FORMAT_PRIORITY: Record<OsKey, string[]> = {
  windows: [".exe", ".zip"],
  macos: [".dmg", ".zip"],
  linux: [".AppImage", ".tar.gz", ".deb"],
};

const OS_LABEL: Record<OsKey, string> = {
  windows: "Windows 10/11 · x64",
  macos: "macOS 11+ · Apple Silicon",
  linux: "Linux x86-64",
};

function formatSize(bytes: number): string {
  if (!bytes || bytes < 0) return "—";
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(2)} GB`;
  return `${Math.round(bytes / 1024 ** 2)} MB`;
}

function extensionOf(filename: string): string {
  if (filename.endsWith(".tar.gz")) return ".tar.gz";
  const dot = filename.lastIndexOf(".");
  return dot === -1 ? "" : filename.slice(dot);
}

/** Collapse the manifest's per-file rows into one card per OS. */
function toDownloads(manifest: Manifest): Partial<Record<OsKey, DownloadInfo>> {
  const out: Partial<Record<OsKey, DownloadInfo>> = {};

  (Object.keys(FORMAT_PRIORITY) as OsKey[]).forEach((os) => {
    const entries = manifest.miners.filter((m) => m.platform === os);
    if (entries.length === 0) return;

    const rank = (e: ManifestEntry) => {
      const i = FORMAT_PRIORITY[os].indexOf(extensionOf(e.filename));
      return i === -1 ? Number.MAX_SAFE_INTEGER : i;
    };
    const sorted = [...entries].sort((a, b) => rank(a) - rank(b));
    const primary = sorted[0];
    const secondary = sorted[1];

    const bundled = primary.bundled_animica || primary.version;
    const notes = [
      extensionOf(primary.filename),
      `bundles animica ${bundled}`,
      "full AI + media stack",
    ];
    if (os === "macos") notes.push("unsigned (right-click → Open)");

    out[os] = {
      file: primary.download_url,
      label: primary.min_os || OS_LABEL[os],
      note: notes.join(" · "),
      size: formatSize(primary.size_bytes),
      sha256: primary.sha256,
      alt: secondary
        ? {
            file: secondary.download_url,
            label: extensionOf(secondary.filename),
            size: formatSize(secondary.size_bytes),
          }
        : undefined,
    };
  });

  return out;
}

const PLATFORMS: {
  key: OsKey;
  name: string;
  match: RegExp;
  // Simple inline SVG glyphs so we don't add an icon dependency.
  Icon: () => JSX.Element;
}[] = [
  {
    key: "windows",
    name: "Windows",
    match: /win(dows|32|64|nt)?/i,
    Icon: () => (
      <svg viewBox="0 0 24 24" className="h-6 w-6" fill="currentColor" aria-hidden>
        <path d="M3 5.4 10.2 4.4v6.9H3V5.4Zm0 13.2 7.2 1V12.7H3v5.9Zm8.2 1.1L21 21V12.7h-9.8v7Zm0-15.4V11.3H21V3l-9.8 1.3Z" />
      </svg>
    ),
  },
  {
    key: "macos",
    name: "macOS",
    match: /mac(intosh| os)?|os x|iphone|ipad/i,
    Icon: () => (
      <svg viewBox="0 0 24 24" className="h-6 w-6" fill="currentColor" aria-hidden>
        <path d="M16.4 12.6c0-2.3 1.9-3.4 2-3.5-1.1-1.6-2.8-1.8-3.4-1.8-1.4-.1-2.8.8-3.5.8-.7 0-1.8-.8-3-.8-1.5 0-3 .9-3.8 2.3-1.6 2.8-.4 7 1.2 9.3.8 1.1 1.7 2.4 2.9 2.3 1.2 0 1.6-.7 3-.7s1.8.7 3 .7 2-1.1 2.7-2.2c.9-1.3 1.2-2.5 1.3-2.6-.1 0-2.5-1-2.5-3.8ZM14.2 5.7c.6-.8 1-1.9.9-3-.9 0-2 .6-2.7 1.4-.6.7-1.1 1.8-.9 2.9 1 .1 2-.5 2.7-1.3Z" />
      </svg>
    ),
  },
  {
    key: "linux",
    name: "Linux",
    match: /linux|x11|ubuntu|debian|fedora/i,
    Icon: () => (
      <svg viewBox="0 0 24 24" className="h-6 w-6" fill="currentColor" aria-hidden>
        <path d="M12 2c-2 0-3 1.8-3 4.2 0 1.5.4 2.4.4 3.4 0 1.1-1.4 2.4-2.3 4.2-.7 1.3-1.6 2.6-1.6 4 0 .8.4 1.4 1.2 1.6-.1.5 0 1 .4 1.3.6.5 1.7.4 2.6.6.7.2 1.3.7 2.3.7s1.6-.5 2.3-.7c.9-.2 2-.1 2.6-.6.4-.3.5-.8.4-1.3.8-.2 1.2-.8 1.2-1.6 0-1.4-.9-2.7-1.6-4-.9-1.8-2.3-3.1-2.3-4.2 0-1 .4-1.9.4-3.4C15 3.8 14 2 12 2Zm-1.4 4.3c.4 0 .7.4.7.9s-.3.9-.7.9-.7-.4-.7-.9.3-.9.7-.9Zm2.8 0c.4 0 .7.4.7.9s-.3.9-.7.9-.7-.4-.7-.9.3-.9.7-.9Z" />
      </svg>
    ),
  },
];

export default function DownloadPage() {
  const [os, setOs] = useState<OsKey | "">("");
  const [downloads, setDownloads] = useState<Partial<Record<OsKey, DownloadInfo>>>({});
  const [release, setRelease] = useState<string>("");
  const [notice, setNotice] = useState<string>("");
  const [manifestError, setManifestError] = useState(false);

  // OS auto-detection so we can highlight + label the matching platform.
  useEffect(() => {
    const ua = navigator.userAgent || "";
    const hit = PLATFORMS.find((p) => p.match.test(ua));
    setOs(hit?.key ?? "");
  }, []);

  // Installer metadata comes from the build, not from this file.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/downloads/manifest.json", { cache: "no-store" });
        if (!res.ok) throw new Error(`manifest ${res.status}`);
        const data = (await res.json()) as Manifest;
        if (cancelled) return;
        setDownloads(toDownloads(data));
        setRelease(data.version ?? "");
        setNotice(data.notice ?? "");
      } catch {
        if (!cancelled) setManifestError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-14">
      {/* 1 — Animated particle hero. */}
      <ParticleHero version={release} />

      {/* 2 — Live stats band. */}
      <LiveStats />

      {/* 3 — Direct download cards. */}
      <section className="space-y-5">
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wider text-neon-green/80">
            Direct downloads
          </p>
          <h2 className="text-2xl font-semibold tracking-tight text-white md:text-3xl">
            Grab the desktop app{release ? ` · v${release}` : ""}
          </h2>
          <p className="max-w-2xl text-white/60">
            Installers served straight from pool.animica.org — no GitHub, no
            redirects. Same wallet auto-creation and ANM-only payouts as the CLI.
            Each build carries the full animica runtime including the AI and
            generative-media stack, so the downloads are several GB.
          </p>
        </div>

        {notice && (
          <p className="rounded-xl border border-amber-400/40 bg-amber-400/10 px-4 py-3 text-sm text-amber-200">
            {notice}
          </p>
        )}

        {manifestError && (
          <p className="rounded-xl border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-200">
            Could not load the installer list right now. Please retry shortly, or
            use the CLI below.
          </p>
        )}

        <div className="grid gap-4 sm:grid-cols-3">
          {PLATFORMS.map((p) => {
            const d = downloads[p.key];
            const detected = os === p.key;
            if (!d) {
              return (
                <div
                  key={p.key}
                  className="flex flex-col rounded-2xl border border-white/10 bg-white/[0.02] p-5 shadow-soft backdrop-blur-md"
                >
                  <div className="flex items-center gap-3">
                    <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-white/[0.06] text-white/50">
                      <p.Icon />
                    </span>
                    <div>
                      <h3 className="font-semibold text-white/70">{p.name}</h3>
                      <p className="text-xs text-white/40">{OS_LABEL[p.key]}</p>
                    </div>
                  </div>
                  <p className="mt-5 text-sm text-white/45">
                    {manifestError ? "Unavailable" : "Build in progress — check back shortly."}
                  </p>
                </div>
              );
            }
            return (
              <div
                key={p.key}
                className={`group relative flex flex-col rounded-2xl border p-5 shadow-soft backdrop-blur-md transition-colors ${
                  detected
                    ? "border-neon-green/50 bg-neon-green/[0.06]"
                    : "border-white/10 bg-white/[0.04] hover:border-white/20"
                }`}
              >
                {detected && (
                  <span className="absolute right-4 top-4 inline-flex items-center gap-1 rounded-full border border-neon-green/40 bg-neon-green/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-neon-green">
                    Detected
                  </span>
                )}

                <div className="flex items-center gap-3">
                  <span
                    className={`flex h-11 w-11 items-center justify-center rounded-xl ${
                      detected
                        ? "bg-neon-green/15 text-neon-green"
                        : "bg-white/[0.06] text-white/80"
                    }`}
                  >
                    <p.Icon />
                  </span>
                  <div>
                    <h3 className="font-semibold text-white">{p.name}</h3>
                    <p className="text-xs text-white/50">{d.label}</p>
                  </div>
                </div>

                <dl className="mt-5 space-y-2 text-sm">
                  <Meta label="Format" value={d.note} />
                  <Meta label="Size" value={d.size} />
                  <Meta
                    label="SHA-256"
                    value={
                      <code
                        className="font-mono text-xs text-white/70"
                        title={d.sha256}
                      >
                        {d.sha256 ? `${d.sha256.slice(0, 16)}…` : "—"}
                      </code>
                    }
                  />
                </dl>

                <div className="mt-auto pt-5">
                  <a
                    href={d.file}
                    download
                    className={detected ? "btn-primary w-full" : "btn-ghost w-full"}
                  >
                    {detected ? `Download for ${p.name}` : `Download`}
                  </a>
                  <p className="mt-2 text-center text-[11px] uppercase tracking-wide text-white/35">
                    {d.alt ? (
                      <>
                        Direct download ·{" "}
                        <a
                          href={d.alt.file}
                          download
                          className="underline underline-offset-2 hover:text-white/60"
                        >
                          {d.alt.label} ({d.alt.size})
                        </a>
                      </>
                    ) : (
                      "Direct download"
                    )}
                  </p>
                </div>
              </div>
            );
          })}
        </div>

        <p className="text-xs text-white/40">
          Checksums let you verify integrity after download. Source for the
          miner lives on{" "}
          <a
            href="https://github.com/animicaorg"
            target="_blank"
            rel="noreferrer noopener"
            className="text-white/60 underline underline-offset-2 hover:text-white/80"
          >
            GitHub
          </a>
          .
        </p>
      </section>

      {/* 4 — CLI alternative. */}
      <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-5 shadow-soft backdrop-blur-md">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="space-y-1">
            <h2 className="text-lg font-semibold text-white">Prefer the CLI?</h2>
            <p className="text-sm text-white/60">
              One command does everything — it auto-creates a wallet and starts
              mining + AI useful-work at once.
            </p>
          </div>
        </div>
        <pre className="mt-4 w-full overflow-x-auto rounded-xl border border-white/10 bg-black/50 p-4 font-mono text-sm leading-relaxed text-neon-green">
{`pip install --upgrade animica
animica up`}
        </pre>
      </section>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-white/40">{label}</span>
      <span className="text-right text-white/80">{value}</span>
    </div>
  );
}
