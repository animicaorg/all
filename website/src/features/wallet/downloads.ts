import { existsSync, readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';

export interface WalletManifestPlatform {
  architecture?: string;
  build_label?: string;
  checksum_url?: string;
  checksum_filename?: string;
  checksum_size_bytes?: number;
  installer_url?: string;
  installer_filename?: string;
  installer_sha256?: string;
  installer_size_bytes?: number;
  zip_url?: string;
  zip_filename?: string;
  zip_sha256?: string;
  zip_size_bytes?: number;
  appimage_url?: string;
  appimage_filename?: string;
  appimage_sha256?: string;
  appimage_size_bytes?: number;
  deb_url?: string;
  deb_filename?: string;
  deb_sha256?: string;
  deb_size_bytes?: number;
  tarball_url?: string;
  tarball_filename?: string;
  tarball_sha256?: string;
  tarball_size_bytes?: number;
  raw_url?: string;
  raw_filename?: string;
  raw_sha256?: string;
  raw_size_bytes?: number;
  package_checksum_url?: string;
  package_checksum_filename?: string;
  package_checksum_size_bytes?: number;
  raw_checksum_url?: string;
  raw_checksum_filename?: string;
  raw_checksum_size_bytes?: number;
}

export interface WalletManifest {
  version?: string;
  generated_at?: string;
  windows?: WalletManifestPlatform;
  macos?: WalletManifestPlatform;
  linux?: WalletManifestPlatform;
}

export interface WalletDownloadEntry {
  href: string;
  download: string;
  label: string;
  primary?: boolean;
  sha256?: string;
  sizeBytes?: number;
  sizeLabel?: string;
}

export interface WalletChecksumLink {
  href: string;
  label: string;
}

export interface WalletPlatformCard {
  key: 'windows' | 'macos' | 'linux';
  title: string;
  description: string;
  architecture?: string;
  buildLabel?: string;
  downloads: WalletDownloadEntry[];
  checksums: WalletChecksumLink[];
  instructions: string[];
}

export interface WalletDownloadPageData {
  versionLabel?: string;
  generatedAt?: string;
  platformCards: WalletPlatformCard[];
}

export interface WalletExtensionDownloadData {
  download?: WalletDownloadEntry;
  checksumLink?: WalletChecksumLink;
  instructions: string[];
}

const walletPublicDirCandidates: Array<string | URL> = [
  new URL('../../../public/wallet/', import.meta.url),
  resolve(process.cwd(), 'public', 'wallet'),
];
const walletManifestCandidates: Array<string | URL> = [
  new URL('../../../public/wallet/manifest.json', import.meta.url),
  resolve(process.cwd(), 'public', 'wallet', 'manifest.json'),
];

function findPublicWalletFile(filename: string): string | URL | null {
  for (const basePath of walletPublicDirCandidates) {
    const candidate = basePath instanceof URL ? new URL(filename, basePath) : resolve(basePath, filename);
    if (existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

function hasPublicWalletFile(filename: string): boolean {
  return findPublicWalletFile(filename) !== null;
}

function readPublicWalletFile(filename: string): string | undefined {
  const candidate = findPublicWalletFile(filename);
  if (!candidate) {
    return undefined;
  }

  try {
    return readFileSync(candidate, 'utf-8');
  } catch {
    return undefined;
  }
}

function readPublicWalletFileSize(filename: string): number | undefined {
  const candidate = findPublicWalletFile(filename);
  if (!candidate) {
    return undefined;
  }

  try {
    return statSync(candidate).size;
  } catch {
    return undefined;
  }
}

function parseSha256(checksumFileContents: string | undefined): string | undefined {
  if (!checksumFileContents) {
    return undefined;
  }
  const match = checksumFileContents.match(/\b[a-fA-F0-9]{64}\b/);
  return match?.[0]?.toLowerCase();
}

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

function readManifestFile(): WalletManifest | null {
  for (const manifestPath of walletManifestCandidates) {
    if (!existsSync(manifestPath)) {
      continue;
    }
    try {
      return JSON.parse(readFileSync(manifestPath, 'utf-8')) as WalletManifest;
    } catch {
      continue;
    }
  }
  return null;
}

function normalizeDownload(
  href: string | undefined,
  download: string | undefined,
  label: string,
  sizeBytes?: number,
  sha256?: string,
  primary?: boolean,
): WalletDownloadEntry | null {
  if (!href || !download) {
    return null;
  }

  return {
    href,
    download,
    label,
    primary,
    sha256,
    sizeBytes,
    sizeLabel: formatBytes(sizeBytes),
  };
}

export function normalizeWalletManifest(manifest: WalletManifest | null): WalletPlatformCard[] {
  if (!manifest) {
    return [];
  }

  const cards: WalletPlatformCard[] = [];

  if (manifest.windows) {
    const windowsDownloads = [
      normalizeDownload(
        manifest.windows.installer_url,
        manifest.windows.installer_filename,
        'Windows Installer (.exe)',
        manifest.windows.installer_size_bytes,
        manifest.windows.installer_sha256,
        true,
      ),
      normalizeDownload(
        manifest.windows.zip_url,
        manifest.windows.zip_filename,
        'Portable ZIP',
        manifest.windows.zip_size_bytes,
        manifest.windows.zip_sha256,
      ),
    ].filter((entry): entry is WalletDownloadEntry => entry !== null);

    if (windowsDownloads.length > 0) {
      const checksums: WalletChecksumLink[] = [];
      if (manifest.windows.checksum_url) {
        checksums.push({
          href: manifest.windows.checksum_url,
          label: 'SHA-256 checksums',
        });
      }

      cards.push({
        key: 'windows',
        title: 'Windows',
        description: 'Linux cross-built download artifacts for Windows 10/11 on x86_64.',
        architecture: manifest.windows.architecture,
        buildLabel: manifest.windows.build_label ?? manifest.version,
        downloads: windowsDownloads,
        checksums,
        instructions: [
          'Download the installer and run it on Windows.',
          'If SmartScreen warns because the build is unsigned, click “More info” and verify the SHA-256 checksum before running.',
          'Use the portable ZIP only if you need a non-installed copy.',
        ],
      });
    }
  }

  if (manifest.macos) {
    const macDownloads = [
      normalizeDownload(
        manifest.macos.zip_url,
        manifest.macos.zip_filename,
        'Portable ZIP',
        manifest.macos.zip_size_bytes,
        manifest.macos.zip_sha256,
        true,
      ),
    ].filter((entry): entry is WalletDownloadEntry => entry !== null);

    if (macDownloads.length > 0) {
      const checksums: WalletChecksumLink[] = [];
      if (manifest.macos.checksum_url) {
        checksums.push({
          href: manifest.macos.checksum_url,
          label: macDownloads.length > 1 ? 'SHA-256 checksums' : 'SHA-256 checksum',
        });
      }

      cards.push({
        key: 'macos',
        title: 'macOS',
        description: 'Native macOS desktop builds for Intel and Apple Silicon when available.',
        architecture: manifest.macos.architecture,
        buildLabel: manifest.macos.build_label ?? manifest.version,
        downloads: macDownloads,
        checksums,
        instructions: [
          'Download the ZIP and extract Animica Wallet.',
          'If Gatekeeper warns, verify the SHA-256 checksum before first launch.',
        ],
      });
    }
  }

  if (manifest.linux) {
    const linuxDownloads = [
      normalizeDownload(
        manifest.linux.appimage_url,
        manifest.linux.appimage_filename,
        'AppImage',
        manifest.linux.appimage_size_bytes,
        manifest.linux.appimage_sha256,
        true,
      ),
      normalizeDownload(
        manifest.linux.deb_url,
        manifest.linux.deb_filename,
        '.deb Package',
        manifest.linux.deb_size_bytes,
        manifest.linux.deb_sha256,
      ),
      normalizeDownload(
        manifest.linux.tarball_url,
        manifest.linux.tarball_filename,
        'Portable tar.gz',
        manifest.linux.tarball_size_bytes,
        manifest.linux.tarball_sha256,
      ),
      normalizeDownload(
        manifest.linux.raw_url,
        manifest.linux.raw_filename,
        'Raw executable',
        manifest.linux.raw_size_bytes,
        manifest.linux.raw_sha256,
      ),
    ].filter((entry): entry is WalletDownloadEntry => entry !== null);

    if (linuxDownloads.length > 0) {
      const checksums: WalletChecksumLink[] = [];
      if (manifest.linux.package_checksum_url) {
        checksums.push({
          href: manifest.linux.package_checksum_url,
          label: 'Package SHA-256 checksums',
        });
      }
      if (manifest.linux.raw_checksum_url) {
        checksums.push({
          href: manifest.linux.raw_checksum_url,
          label: 'Raw executable SHA-256 checksum',
        });
      }

      cards.push({
        key: 'linux',
        title: 'Linux',
        description: 'Native Linux builds with bundled runtime assets when those artifacts are published.',
        architecture: manifest.linux.architecture,
        buildLabel: manifest.linux.build_label ?? manifest.version,
        downloads: linuxDownloads,
        checksums,
        instructions: [
          'Choose AppImage for the simplest desktop launch.',
          'Use the .deb package for Debian or Ubuntu installs.',
          'Verify the matching SHA-256 file before first launch.',
        ],
      });
    }
  }

  return cards;
}

function buildLegacyMacCard(): WalletPlatformCard | null {
  const downloads = [
    hasPublicWalletFile('animicawalletmac.zip')
      ? normalizeDownload('/wallet/animicawalletmac.zip', 'animicawalletmac.zip', 'Portable ZIP', undefined, undefined, true)
      : null,
  ].filter((entry): entry is WalletDownloadEntry => entry !== null);

  if (downloads.length === 0) {
    return null;
  }

  const checksums: WalletChecksumLink[] = [];
  if (hasPublicWalletFile('animicawallet.sha256')) {
    checksums.push({
      href: '/wallet/animicawallet.sha256',
      label: downloads.length > 1 ? 'SHA-256 checksums' : 'SHA-256 checksum',
    });
  }

  return {
    key: 'macos',
    title: 'macOS',
    description: 'Legacy website wallet files detected without a generated macOS manifest entry.',
    architecture: 'universal',
    downloads,
    checksums,
    instructions: [
      'Download the ZIP and extract Animica Wallet.',
      'If Gatekeeper warns, verify the SHA-256 checksum before first launch.',
    ],
  };
}

function buildLegacyLinuxCard(): WalletPlatformCard | null {
  const downloads = [
    hasPublicWalletFile('animica-wallet-linux.AppImage')
      ? normalizeDownload('/wallet/animica-wallet-linux.AppImage', 'animica-wallet-linux.AppImage', 'AppImage', undefined, undefined, true)
      : null,
    hasPublicWalletFile('animica-wallet-linux.deb')
      ? normalizeDownload('/wallet/animica-wallet-linux.deb', 'animica-wallet-linux.deb', '.deb Package')
      : null,
    hasPublicWalletFile('animica-wallet-linux.tar.gz')
      ? normalizeDownload('/wallet/animica-wallet-linux.tar.gz', 'animica-wallet-linux.tar.gz', 'Portable tar.gz')
      : null,
    hasPublicWalletFile('animica-wallet')
      ? normalizeDownload('/wallet/animica-wallet', 'animica-wallet', 'Raw executable')
      : null,
  ].filter((entry): entry is WalletDownloadEntry => entry !== null);

  if (downloads.length === 0) {
    return null;
  }

  const checksums: WalletChecksumLink[] = [];
  if (hasPublicWalletFile('animica-wallet-linux.sha256')) {
    checksums.push({ href: '/wallet/animica-wallet-linux.sha256', label: 'Package SHA-256 checksums' });
  }
  if (hasPublicWalletFile('animica-wallet.sha256')) {
    checksums.push({ href: '/wallet/animica-wallet.sha256', label: 'Raw executable SHA-256 checksum' });
  }

  return {
    key: 'linux',
    title: 'Linux',
    description: 'Legacy website wallet files detected without a generated manifest.',
    architecture: 'x86_64',
    downloads,
    checksums,
    instructions: [
      'Choose AppImage for the simplest desktop launch.',
      'Use the .deb package for Debian or Ubuntu installs.',
      'Verify the matching SHA-256 file before first launch.',
    ],
  };
}

function buildLegacyWindowsCard(): WalletPlatformCard | null {
  const downloads = [
    hasPublicWalletFile('animicawallersetup.exe')
      ? normalizeDownload(
          '/wallet/animicawallersetup.exe',
          'animicawallersetup.exe',
          'Windows Installer (.exe)',
          undefined,
          undefined,
          true,
        )
      : null,
  ].filter((entry): entry is WalletDownloadEntry => entry !== null);

  if (downloads.length === 0) {
    return null;
  }

  const checksums: WalletChecksumLink[] = [];
  if (hasPublicWalletFile('animicawallersetup.sha256')) {
    checksums.push({ href: '/wallet/animicawallersetup.sha256', label: 'SHA-256 checksum' });
  }

  return {
    key: 'windows',
    title: 'Windows',
    description: 'Legacy website wallet files detected without a generated Windows manifest entry.',
    architecture: 'x86_64',
    downloads,
    checksums,
    instructions: [
      'Download the installer and run it on Windows.',
      'If SmartScreen warns because the build is unsigned, click “More info” and verify the SHA-256 checksum before running.',
      'Install into your standard user profile unless your organization requires a custom path.',
    ],
  };
}

export function loadWalletDownloadPageData(): WalletDownloadPageData {
  const manifest = readManifestFile();
  const cardsByKey = new Map(
    normalizeWalletManifest(manifest).map((card) => [card.key, card] as const),
  );

  if (!cardsByKey.has('windows')) {
    const legacyWindowsCard = buildLegacyWindowsCard();
    if (legacyWindowsCard) {
      cardsByKey.set('windows', legacyWindowsCard);
    }
  }

  if (!cardsByKey.has('macos')) {
    const legacyMacCard = buildLegacyMacCard();
    if (legacyMacCard) {
      cardsByKey.set('macos', legacyMacCard);
    }
  }

  if (!cardsByKey.has('linux')) {
    const legacyLinuxCard = buildLegacyLinuxCard();
    if (legacyLinuxCard) {
      cardsByKey.set('linux', legacyLinuxCard);
    }
  }

  const platformCards: WalletPlatformCard[] = (['windows', 'macos', 'linux'] as const)
    .map((key) => cardsByKey.get(key))
    .filter((card): card is WalletPlatformCard => card !== undefined);

  return {
    versionLabel: manifest?.version,
    generatedAt: manifest?.generated_at,
    platformCards,
  };
}

export function loadWalletExtensionDownloadData(): WalletExtensionDownloadData {
  const zipFilename = 'animica-wallet-extension-chrome.zip';
  const checksumFilename = 'animica-wallet-extension-chrome.sha256';
  const checksumContents = readPublicWalletFile(checksumFilename);
  const checksum = parseSha256(checksumContents);

  return {
    download: hasPublicWalletFile(zipFilename)
      ? normalizeDownload(
          `/wallet/${zipFilename}`,
          zipFilename,
          'Chrome / Chromium Extension (.zip)',
          readPublicWalletFileSize(zipFilename),
          checksum,
          true,
        ) ?? undefined
      : undefined,
    checksumLink: hasPublicWalletFile(checksumFilename)
      ? {
          href: `/wallet/${checksumFilename}`,
          label: 'SHA-256 checksum',
        }
      : undefined,
    instructions: [
      'Download the ZIP and extract it locally.',
      'Open chrome://extensions, enable Developer mode, and click “Load unpacked”.',
      'Select the extracted folder containing manifest.json and verify the checksum before first use.',
    ],
  };
}
