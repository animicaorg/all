// GET /app/api/downloads — returns the GUI-miner downloads manifest.
//
// The browser never needs to know WHERE the manifest lives; this route resolves
// it server-side in three tiers, most-authoritative first:
//   1. DOWNLOADS_MANIFEST_URL env set  → fetch it (lets ops point at an object
//      store / GitHub-hosted manifest without redeploying the app).
//   2. bundled public/downloads/manifest.json → the seed manifest shipped in repo.
//   3. inline default → minimal manifest pointing at the GH-release URL pattern,
//      so the page still renders even if the file is missing.
//
// Mirrors src/app/api/pools/route.ts: force-dynamic, server-side source kept off
// the browser, graceful degradation on error.
import { NextResponse } from "next/server";
import { readFile } from "node:fs/promises";
import path from "node:path";

export const dynamic = "force-dynamic";

interface MinerDownload {
  platform: string; // "windows" | "macos" | "linux"
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

// GitHub Releases is where the big PyInstaller binaries live (per build-scripts).
const GH_RELEASE_BASE =
  "https://github.com/animicaorg/all/releases/download/miner-gui-v0.1.0";

// Tier 3: shipped even if the bundled file is unreadable. Same shape the page
// expects so it always has all three platforms to render.
const INLINE_DEFAULT: DownloadsManifest = {
  version: "1.0.0",
  notes:
    "Inline fallback manifest. GUI miners run the unified Animica flow (mine + AI, ANM-only).",
  miners: [
    {
      platform: "windows",
      name: "Animica Miner GUI for Windows",
      version: "0.1.0",
      download_url: `${GH_RELEASE_BASE}/Animica-Miner-GUI-0.1.0-Windows-x64.zip`,
      size_bytes: 0,
      checksum_sha256: "TBD",
      released_at: null,
    },
    {
      platform: "macos",
      name: "Animica Miner GUI for macOS",
      version: "0.1.0",
      download_url: `${GH_RELEASE_BASE}/Animica-Miner-GUI-0.1.0-macOS-arm64.dmg`,
      size_bytes: 0,
      checksum_sha256: "TBD",
      released_at: null,
    },
    {
      platform: "linux",
      name: "Animica Miner GUI for Linux",
      version: "0.1.0",
      download_url: `${GH_RELEASE_BASE}/Animica-Miner-GUI-0.1.0-Linux-x86_64.tar.gz`,
      size_bytes: 0,
      checksum_sha256: "TBD",
      released_at: null,
    },
  ],
};

function isManifest(value: unknown): value is DownloadsManifest {
  if (!value || typeof value !== "object") return false;
  const m = value as Record<string, unknown>;
  return typeof m.version === "string" && Array.isArray(m.miners);
}

async function fromRemote(url: string): Promise<DownloadsManifest | null> {
  try {
    const res = await fetch(url, {
      cache: "no-store",
      // Don't hang the route on a slow object store.
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return null;
    const data: unknown = await res.json();
    return isManifest(data) ? data : null;
  } catch {
    return null;
  }
}

async function fromBundled(): Promise<DownloadsManifest | null> {
  try {
    const file = path.join(process.cwd(), "public", "downloads", "manifest.json");
    const raw = await readFile(file, "utf-8");
    const data: unknown = JSON.parse(raw);
    return isManifest(data) ? data : null;
  } catch {
    return null;
  }
}

export async function GET() {
  try {
    const remoteUrl = process.env.DOWNLOADS_MANIFEST_URL;
    const manifest =
      (remoteUrl ? await fromRemote(remoteUrl) : null) ??
      (await fromBundled()) ??
      INLINE_DEFAULT;
    return NextResponse.json(manifest);
  } catch {
    // Never 500 the page over a downloads listing — fall back to the inline set.
    return NextResponse.json(INLINE_DEFAULT, { status: 200 });
  }
}
