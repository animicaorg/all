import { existsSync, readFileSync, statSync } from 'node:fs';

export interface StudioManifestLinux {
  architecture?: string;
  build_label?: string;
  deb_url?: string;
  deb_filename?: string;
  deb_sha256?: string;
  deb_size_bytes?: number;
  checksum_url?: string;
  checksum_filename?: string;
  checksum_size_bytes?: number;
}

export interface StudioManifest {
  version?: string;
  generated_at?: string;
  linux?: StudioManifestLinux;
}

export interface StudioDownloadEntry {
  href: string;
  download: string;
  label: string;
  sha256?: string;
  sizeBytes?: number;
  sizeLabel?: string;
}

export interface StudioChecksumLink {
  href: string;
  label: string;
}

export interface StudioDownloadPageData {
  versionLabel?: string;
  generatedAt?: string;
  architecture?: string;
  buildLabel?: string;
  linuxDownload?: StudioDownloadEntry;
  checksumLink?: StudioChecksumLink;
  instructions: string[];
}

const studioPublicDir = new URL('../../../public/studio/', import.meta.url);
const studioManifestPath = new URL('../../../public/studio/manifest.json', import.meta.url);

function formatBytes(bytes?: number): string | undefined {
  if (!bytes || bytes <= 0) return undefined;
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const rounded = value >= 10 || unitIndex === 0 ? value.toFixed(0) : value.toFixed(1);
  return `${rounded} ${units[unitIndex]}`;
}

function readManifestFile(): StudioManifest | null {
  if (!existsSync(studioManifestPath)) {
    return null;
  }

  try {
    return JSON.parse(readFileSync(studioManifestPath, 'utf-8')) as StudioManifest;
  } catch {
    return null;
  }
}

function normalizeDownload(
  href: string | undefined,
  download: string | undefined,
  label: string,
  sizeBytes?: number,
  sha256?: string,
): StudioDownloadEntry | undefined {
  if (!href || !download) {
    return undefined;
  }

  return {
    href,
    download,
    label,
    sha256,
    sizeBytes,
    sizeLabel: formatBytes(sizeBytes),
  };
}

export function normalizeStudioManifest(manifest: StudioManifest | null): StudioDownloadPageData {
  if (!manifest?.linux) {
    return buildLegacyStudioDownloadData();
  }

  return {
    versionLabel: manifest.version,
    generatedAt: manifest.generated_at,
    architecture: manifest.linux.architecture,
    buildLabel: manifest.linux.build_label ?? manifest.version,
    linuxDownload: normalizeDownload(
      manifest.linux.deb_url,
      manifest.linux.deb_filename,
      'Download Linux .deb',
      manifest.linux.deb_size_bytes,
      manifest.linux.deb_sha256,
    ),
    checksumLink:
      manifest.linux.checksum_url && manifest.linux.checksum_filename
        ? {
            href: manifest.linux.checksum_url,
            label: 'SHA-256 checksum',
          }
        : undefined,
    instructions: [
      'Download the .deb package on Debian, Ubuntu, or another compatible Linux distribution.',
      'Install with your package manager or run `sudo dpkg -i animica-studio-linux-amd64.deb`.',
      'Verify the SHA-256 checksum before first launch when distributing outside a signed channel.',
    ],
  };
}

function buildLegacyStudioDownloadData(): StudioDownloadPageData {
  const debPath = new URL('animica-studio-linux-amd64.deb', studioPublicDir);
  const checksumPath = new URL('animica-studio-linux.sha256', studioPublicDir);

  const linuxDownload = existsSync(debPath)
    ? normalizeDownload(
        '/studio/animica-studio-linux-amd64.deb',
        'animica-studio-linux-amd64.deb',
        'Download Linux .deb',
        statSync(debPath).size,
      )
    : undefined;

  return {
    linuxDownload,
    checksumLink: existsSync(checksumPath)
      ? {
          href: '/studio/animica-studio-linux.sha256',
          label: 'SHA-256 checksum',
        }
      : undefined,
    instructions: [
      'Download the .deb package on Debian, Ubuntu, or another compatible Linux distribution.',
      'Install with your package manager or run `sudo dpkg -i animica-studio-linux-amd64.deb`.',
      'Verify the SHA-256 checksum before first launch when distributing outside a signed channel.',
    ],
  };
}

export function loadStudioDownloadPageData(): StudioDownloadPageData {
  return normalizeStudioManifest(readManifestFile());
}
