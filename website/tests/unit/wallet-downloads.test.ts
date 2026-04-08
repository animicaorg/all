import { describe, expect, it } from 'vitest';

import { loadWalletDownloadPageData, normalizeWalletManifest, type WalletManifest } from '../../src/features/wallet/downloads';

describe('wallet download manifest normalization', () => {
  it('handles a Windows-only manifest', () => {
    const manifest: WalletManifest = {
      version: 'v1.2.3-test',
      generated_at: '2026-04-08T00:00:00Z',
      windows: {
        architecture: 'x86_64',
        build_label: 'v1.2.3-test',
        installer_url: '/wallet/animica-wallet-windows-x64.exe',
        installer_filename: 'animica-wallet-windows-x64.exe',
        installer_sha256: 'deadbeef',
        installer_size_bytes: 1024,
        zip_url: '/wallet/animica-wallet-windows-x64.zip',
        zip_filename: 'animica-wallet-windows-x64.zip',
        zip_sha256: 'cafebabe',
        zip_size_bytes: 2048,
        checksum_url: '/wallet/animica-wallet-windows.sha256',
        checksum_filename: 'animica-wallet-windows.sha256',
      },
    };

    const cards = normalizeWalletManifest(manifest);

    expect(cards).toHaveLength(1);
    expect(cards[0]?.key).toBe('windows');
    expect(cards[0]?.downloads).toHaveLength(2);
    expect(cards[0]?.checksums[0]?.href).toBe('/wallet/animica-wallet-windows.sha256');
  });

  it('gracefully handles absent platforms', () => {
    const cards = normalizeWalletManifest({ version: 'v1.2.3-test' });
    expect(cards).toEqual([]);
  });

  it('renders Linux files without requiring every optional artifact', () => {
    const manifest: WalletManifest = {
      version: 'v1.2.3-test',
      linux: {
        architecture: 'x86_64',
        build_label: 'v1.2.3-test',
        deb_url: '/wallet/animica-wallet-linux.deb',
        deb_filename: 'animica-wallet-linux.deb',
        deb_sha256: 'deadbeef',
        deb_size_bytes: 4096,
        package_checksum_url: '/wallet/animica-wallet-linux.sha256',
        package_checksum_filename: 'animica-wallet-linux.sha256',
      },
    };

    const cards = normalizeWalletManifest(manifest);

    expect(cards).toHaveLength(1);
    expect(cards[0]?.key).toBe('linux');
    expect(cards[0]?.downloads).toHaveLength(1);
    expect(cards[0]?.downloads[0]?.label).toBe('.deb Package');
  });
});

describe('wallet download page data', () => {
  it('does not crash when no manifest is present', () => {
    const data = loadWalletDownloadPageData();
    expect(Array.isArray(data.platformCards)).toBe(true);
  });
});
