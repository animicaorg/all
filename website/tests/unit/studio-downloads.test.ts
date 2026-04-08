import { describe, expect, it } from 'vitest';

import { loadStudioDownloadPageData, normalizeStudioManifest, type StudioManifest } from '../../src/features/studio/downloads';

describe('studio download manifest normalization', () => {
  it('renders the linux deb entry from manifest data', () => {
    const manifest: StudioManifest = {
      version: 'v0.1.0-test',
      generated_at: '2026-04-08T00:00:00Z',
      linux: {
        architecture: 'amd64',
        build_label: 'v0.1.0-test',
        deb_url: '/studio/animica-studio-linux-amd64.deb',
        deb_filename: 'animica-studio-linux-amd64.deb',
        deb_sha256: 'deadbeef',
        deb_size_bytes: 2048,
        checksum_url: '/studio/animica-studio-linux.sha256',
        checksum_filename: 'animica-studio-linux.sha256',
      },
    };

    const data = normalizeStudioManifest(manifest);

    expect(data.buildLabel).toBe('v0.1.0-test');
    expect(data.linuxDownload?.label).toBe('Download Linux .deb');
    expect(data.checksumLink?.href).toBe('/studio/animica-studio-linux.sha256');
  });
});

describe('studio download page data', () => {
  it('does not crash when loading the published data', () => {
    const data = loadStudioDownloadPageData();
    expect(data.instructions.length).toBeGreaterThan(0);
  });
});
