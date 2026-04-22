import { Router } from 'express';
import { promises as fs } from 'fs';
import path from 'path';
import type { AppConfig } from '../../config.js';

type MinerPlatform = 'linux' | 'macos' | 'windows';

type MinerRelease = {
  filename: string;
  version: string;
  network: string;
  platform: MinerPlatform;
  buildId: string;
  extension: string;
  sizeBytes: number;
  modifiedAt: string;
  downloadPath: string;
};

type ProviderPlatform = 'windows' | 'linux' | 'python';

type ProviderManifestItem = {
  platform: ProviderPlatform;
  label: string;
  filename: string;
  version: string;
  size_bytes: number;
  sha256: string;
  url: string;
  release_notes?: string;
  notes?: string;
};

type ProviderManifest = {
  source?: string;
  version?: string;
  generated_at?: string;
  checksum_file?: string;
  items: ProviderManifestItem[];
};

const FILE_PATTERN =
  /^animica-cpu-miner-(?<version>\d+\.\d+\.\d+)-(?<network>[a-z0-9]+)-(?<platform>linux|macos|windows)-(?<build>[a-f0-9]+)\.(?<ext>tar\.gz|zip)$/i;

function parseSemver(version: string): [number, number, number] {
  const [major, minor, patch] = version.split('.').map((part) => Number(part) || 0);
  return [major, minor, patch];
}

function compareSemverDesc(a: string, b: string): number {
  const av = parseSemver(a);
  const bv = parseSemver(b);
  if (av[0] !== bv[0]) return bv[0] - av[0];
  if (av[1] !== bv[1]) return bv[1] - av[1];
  return bv[2] - av[2];
}

function platformOrder(platform: MinerPlatform): number {
  switch (platform) {
    case 'linux':
      return 0;
    case 'macos':
      return 1;
    case 'windows':
      return 2;
    default:
      return 99;
  }
}

function resolveArtifactsDir(config: AppConfig): string {
  const configured = config.AICF_MINER_ARTIFACTS_DIR;
  if (path.isAbsolute(configured)) {
    return configured;
  }
  const cwdRelative = path.resolve(process.cwd(), configured);
  return cwdRelative;
}

function resolveProviderArtifactsDir(config: AppConfig): string {
  const configured = config.AICF_PROVIDER_ARTIFACTS_DIR;
  if (path.isAbsolute(configured)) {
    return configured;
  }
  return path.resolve(process.cwd(), configured);
}

async function readProviderManifest(dir: string): Promise<ProviderManifest> {
  const manifestPath = path.join(dir, 'manifest.json');
  const raw = await fs.readFile(manifestPath, 'utf-8');
  const parsed = JSON.parse(raw) as ProviderManifest;
  if (!Array.isArray(parsed.items)) {
    throw new Error('Provider manifest is missing items array');
  }
  return parsed;
}

async function resolveProviderArtifactPath(dir: string, filename: string): Promise<string | null> {
  const candidates = [
    path.join(dir, filename),
    path.join(dir, 'windows', filename),
    path.join(dir, 'linux', filename),
    path.join(dir, 'python', filename)
  ];

  for (const candidate of candidates) {
    const stats = await fs.stat(candidate).catch(() => null);
    if (stats?.isFile()) {
      return candidate;
    }
  }

  return null;
}

async function readReleases(dir: string): Promise<MinerRelease[]> {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  const files = entries.filter((entry) => entry.isFile());

  const releases: MinerRelease[] = [];
  for (const file of files) {
    const match = file.name.match(FILE_PATTERN);
    if (!match?.groups) continue;

    const stats = await fs.stat(path.join(dir, file.name));
    const platform = String(match.groups.platform).toLowerCase() as MinerPlatform;
    releases.push({
      filename: file.name,
      version: String(match.groups.version),
      network: String(match.groups.network),
      platform,
      buildId: String(match.groups.build),
      extension: String(match.groups.ext),
      sizeBytes: stats.size,
      modifiedAt: stats.mtime.toISOString(),
      downloadPath: `/downloads/miners/${encodeURIComponent(file.name)}`
    });
  }

  releases.sort((a, b) => {
    const semverCmp = compareSemverDesc(a.version, b.version);
    if (semverCmp !== 0) return semverCmp;
    const timeCmp = b.modifiedAt.localeCompare(a.modifiedAt);
    if (timeCmp !== 0) return timeCmp;
    return platformOrder(a.platform) - platformOrder(b.platform);
  });

  return releases;
}

export function createDownloadsRouter(config: AppConfig) {
  const router = Router();

  router.get('/miners', async (_req, res) => {
    try {
      const dir = resolveArtifactsDir(config);
      const releases = await readReleases(dir);
      const latestByPlatform: Partial<Record<MinerPlatform, MinerRelease>> = {};
      for (const release of releases) {
        if (!latestByPlatform[release.platform]) {
          latestByPlatform[release.platform] = release;
        }
      }
      res.status(200).json({
        generatedAt: new Date().toISOString(),
        releases,
        latestByPlatform
      });
    } catch (error) {
      res.status(500).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/miners/:filename', async (req, res) => {
    try {
      const dir = resolveArtifactsDir(config);
      const requested = String(req.params.filename ?? '').trim();
      const safeName = path.basename(requested);
      if (!safeName || safeName !== requested) {
        res.status(400).json({ error: { message: 'Invalid filename' } });
        return;
      }

      const fullPath = path.join(dir, safeName);
      const stats = await fs.stat(fullPath).catch(() => null);
      if (!stats || !stats.isFile()) {
        res.status(404).json({ error: { message: 'Artifact not found' } });
        return;
      }
      res.download(fullPath, safeName);
    } catch (error) {
      res.status(500).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/providers', async (_req, res) => {
    try {
      const dir = resolveProviderArtifactsDir(config);
      const manifest = await readProviderManifest(dir);
      res.status(200).json({
        generatedAt: new Date().toISOString(),
        manifest
      });
    } catch (error) {
      res.status(500).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/providers/:filename', async (req, res) => {
    try {
      const dir = resolveProviderArtifactsDir(config);
      const requested = String(req.params.filename ?? '').trim();
      const safeName = path.basename(requested);
      if (!safeName || safeName !== requested) {
        res.status(400).json({ error: { message: 'Invalid filename' } });
        return;
      }

      const fullPath = await resolveProviderArtifactPath(dir, safeName);
      if (!fullPath) {
        res.status(404).json({ error: { message: 'Artifact not found' } });
        return;
      }
      res.download(fullPath, safeName);
    } catch (error) {
      res.status(500).json({ error: { message: (error as Error).message } });
    }
  });

  return router;
}
