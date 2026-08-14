import { ApiError } from './api';
import { AppStoreError } from './appStore';

// App Store catalog constants shared by the store routes (route.ts files may only
// export handlers, so shared shapes live here). Release/listing POLICY lives in
// lib/appStore.ts — this file is read-side conveniences only.

export const STORE_TYPES = ['APP', 'DIGITAL_GOOD'] as const;

// Release-channel shape — single source of truth is lib/appStore.ts BUILD_CHANNEL_RE.
export { BUILD_CHANNEL_RE as CHANNEL_RE } from './appStore';

// Latest APPROVED build summary shown in catalog/detail/versions (never the disk path).
export const LATEST_BUILD_SELECT = {
  versionCode: true, versionName: true, channel: true, sha3: true, certSha256: true,
  minSdk: true, targetSdk: true, sizeBytes: true, releaseNotes: true, createdAt: true,
} as const;

// Public URL for a StoreAsset's CID-addressed bytes.
export function assetUrl(cid: string): string {
  return `/api/mkt/v1/content/${cid}`;
}

// Map lib/appStore.ts errors onto the route error shape before err(e).
export function asApiError(e: unknown): unknown {
  return e instanceof AppStoreError ? new ApiError(e.status, e.code, e.message) : e;
}
