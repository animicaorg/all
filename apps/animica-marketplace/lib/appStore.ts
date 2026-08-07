import { randomBytes } from 'node:crypto';
import { rename, unlink } from 'node:fs/promises';
import { prisma } from './db';
import { requireSellerEntitlement } from './plan';
import { uniqueSlug } from './accounts';
import { buildPath, freeDiskBytes, safeStorePath, streamToFile } from './mediaStore';
import type { ApkVerdict } from './apkVerify';

// App Store — listings + durable APK builds (var/media-store/builds). Two halves:
//   1. File lifecycle: an upload streams into an `incoming-*` temp name (swept if
//      abandoned), is verified there, and is only renamed to its AppBuild id once the
//      row exists. Files here have NO TTL — deletion happens exclusively through
//      removeBuildFile() when the owning row goes away. Never TTL-wiped.
//   2. Release policy (all FAIL-CLOSED, enforced here so no route can skip them):
//        * packageName is claimed by the listing at its first build, globally unique;
//        * signer-cert continuity: the first accepted build PINS the cert on the
//          listing, every later build must match (rotation = admin-only);
//        * versionCode strictly monotonic per (listing, channel);
//        * per-publisher byte quota across all their listings' stored builds;
//        * first build of a listing => PENDING_REVIEW (manual admin approval);
//          later same-signer builds auto-approve UNLESS they add sensitive
//          permissions the listing has not already been approved with.
// Signature/badging failures still create an AppBuild row (status CHECKS_FAILED,
// file deleted, reasons in reviewNote) — an audit trail of rejected/hostile uploads —
// while structural conflicts (bad versionCode, foreign packageName, quota) throw.

export const APK_MAX_BYTES = Number(process.env.STORE_APK_MAX_BYTES ?? 512 * 1024 * 1024); // 512 MB
// Per-publisher durable-storage quota (sum of stored build files across all their listings).
export const PUBLISHER_QUOTA_BYTES = BigInt(process.env.STORE_PUBLISHER_QUOTA_BYTES ?? 2 * 1024 * 1024 * 1024); // 2 GiB
// Same hard disk floor the media uploads route enforces — the builds store must never
// fill the disk that runs the node + services either.
const STORE_MIN_FREE_BYTES = Number(process.env.MEDIA_STORE_MIN_FREE_BYTES ?? 20 * 1024 * 1024 * 1024);

export const APP_CATEGORIES = ['GAMES', 'AI_AGENTS', 'TOOLS', 'DEV_APPS', 'MOBILE', 'WEB'] as const;
export type AppCategory = (typeof APP_CATEGORIES)[number];
export const BUILD_CHANNEL_RE = /^[a-z][a-z0-9-]{0,15}$/; // stable, beta, ...

export class AppStoreError extends Error {
  constructor(public code: string, message: string, public status = 400) {
    super(message);
  }
}

// Android applicationId: 2+ dot-separated segments, each starting with a letter.
export function isValidPackageName(s: string | null | undefined): boolean {
  return !!s && s.length <= 255 && /^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$/.test(s);
}

export function normalizeAppCategory(s: string | null | undefined): AppCategory | null {
  if (!s) return null;
  const up = s.trim().toUpperCase().replace(/[\s-]+/g, '_');
  return (APP_CATEGORIES as readonly string[]).includes(up) ? (up as AppCategory) : null;
}

// ── Pure release-policy helpers (unit-tested in scripts/store_apk_selfcheck.ts) ──

// Strictly-monotonic versionCode per (listing, channel). maxExisting is the highest
// versionCode among the channel's non-CHECKS_FAILED builds (null = none yet).
export function versionCodeAllowed(maxExisting: number | null, next: number): boolean {
  return Number.isSafeInteger(next) && next > 0 && (maxExisting == null || next > maxExisting);
}

// Cert continuity: null pin = first build (pin now); otherwise exact match required.
export function certContinuityOk(pinnedCertSha256: string | null, certSha256: string): boolean {
  return pinnedCertSha256 == null || pinnedCertSha256 === certSha256;
}

// Review-status decision for a build that passed all checks.
//   firstBuild            — listing has no APPROVED build yet => manual admin review.
//   newSensitive          — sensitive permissions not present in the latest APPROVED
//                           build => back to manual review even for a pinned signer.
export function decideBuildStatus(opts: {
  hasApprovedBuild: boolean;
  sensitivePermissions: string[];
  approvedPermissions: string[] | null; // permissions of the latest APPROVED build (null = none)
}): 'APPROVED' | 'PENDING_REVIEW' {
  if (!opts.hasApprovedBuild) return 'PENDING_REVIEW';
  const prior = new Set(opts.approvedPermissions ?? []);
  const newSensitive = opts.sensitivePermissions.filter((p) => !prior.has(p));
  return newSensitive.length > 0 ? 'PENDING_REVIEW' : 'APPROVED';
}

export function quotaAllows(usedBytes: bigint, addBytes: bigint | number, quota = PUBLISHER_QUOTA_BYTES): boolean {
  return usedBytes + BigInt(addBytes) <= quota;
}

// ── Listings ─────────────────────────────────────────────────────────────────

export interface CreateAppListingInput {
  ownerId: string;
  name: string;
  slug?: string;
  type?: 'APP' | 'DIGITAL_GOOD';
  appCategory?: string;
  packageName?: string; // may also be claimed later, at first build
  description?: string;
  tagline?: string;
  anmDomain?: string; // owned .anm label for the verified-publisher badge
}

// Create a DRAFT store listing (type APP / DIGITAL_GOOD). packageName (when given) is
// format-validated and reserved immediately so no other listing can squat it.
export async function createAppListing(inp: CreateAppListingInput) {
  const name = (inp.name ?? '').trim();
  if (!name) throw new AppStoreError('bad_request', 'name required');
  const type = inp.type === 'DIGITAL_GOOD' ? 'DIGITAL_GOOD' : 'APP';
  const appCategory = normalizeAppCategory(inp.appCategory);
  if (inp.appCategory && !appCategory) {
    throw new AppStoreError('bad_category', `category must be one of ${APP_CATEGORIES.join(', ')}`);
  }
  let packageName: string | null = null;
  if (inp.packageName != null && inp.packageName !== '') {
    if (type !== 'APP') throw new AppStoreError('bad_request', 'packageName applies only to APP listings');
    if (!isValidPackageName(inp.packageName)) throw new AppStoreError('bad_package_name', 'invalid Android applicationId');
    packageName = inp.packageName;
    const taken = await prisma.listing.findUnique({ where: { packageName }, select: { id: true } });
    if (taken) throw new AppStoreError('package_taken', 'packageName already claimed by another listing', 409);
  }
  const anmDomainId = inp.anmDomain ? (await resolveOwnedDomain(inp.ownerId, inp.anmDomain)).id : null;
  const slug = await uniqueSlug(inp.slug || name);
  return prisma.listing.create({
    data: {
      slug,
      ownerId: inp.ownerId,
      type,
      name: name.slice(0, 120),
      tagline: (inp.tagline ?? '').slice(0, 200),
      description: (inp.description ?? '').slice(0, 8000),
      appCategory,
      packageName,
      anmDomainId,
    },
  });
}

export interface UpdateAppListingPatch {
  name?: string;
  tagline?: string;
  description?: string;
  appCategory?: string;
  coverUrl?: string;
  visibility?: 'PUBLIC' | 'UNLISTED' | 'PRIVATE';
  status?: 'DRAFT' | 'PUBLISHED' | 'DELISTED';
  anmDomain?: string | null; // label; null unlinks
}

// Owner-only metadata/visibility update for a store listing. Deliberately CANNOT touch
// packageName or pinnedCertSha256 — those are claimed/pinned by the build pipeline and
// rotated only by admin action.
export async function updateAppListing(accountId: string, slug: string, patch: UpdateAppListingPatch) {
  const listing = await prisma.listing.findUnique({
    where: { slug },
    select: { id: true, ownerId: true, type: true, status: true },
  });
  if (!listing || (listing.type !== 'APP' && listing.type !== 'DIGITAL_GOOD')) {
    throw new AppStoreError('not_found', 'store listing not found', 404);
  }
  if (listing.ownerId !== accountId) throw new AppStoreError('forbidden', 'not your listing', 403);

  const data: Record<string, unknown> = {};
  if (typeof patch.name === 'string' && patch.name.trim()) data.name = patch.name.trim().slice(0, 120);
  if (typeof patch.tagline === 'string') data.tagline = patch.tagline.slice(0, 200);
  if (typeof patch.description === 'string') data.description = patch.description.slice(0, 8000);
  if (typeof patch.coverUrl === 'string') data.coverUrl = patch.coverUrl.slice(0, 500);
  if (patch.appCategory !== undefined) {
    const cat = normalizeAppCategory(patch.appCategory);
    if (!cat) throw new AppStoreError('bad_category', `category must be one of ${APP_CATEGORIES.join(', ')}`);
    data.appCategory = cat;
  }
  if (patch.visibility !== undefined) {
    if (!['PUBLIC', 'UNLISTED', 'PRIVATE'].includes(patch.visibility)) throw new AppStoreError('bad_request', 'bad visibility');
    data.visibility = patch.visibility;
  }
  if (patch.status !== undefined) {
    if (!['DRAFT', 'PUBLISHED', 'DELISTED'].includes(patch.status)) throw new AppStoreError('bad_request', 'bad status');
    // Publishing an APP requires at least one APPROVED build — an empty store page
    // with a BUY button would sell an uninstallable promise.
    if (patch.status === 'PUBLISHED' && listing.status !== 'PUBLISHED') {
      const approved = await prisma.appBuild.count({ where: { listingId: listing.id, status: 'APPROVED' } });
      if (approved === 0 && listing.type === 'APP') {
        throw new AppStoreError('no_approved_build', 'publish requires an APPROVED build', 409);
      }
      // Free cannot SELL: going live with an ACTIVE paid price requires marketplace_selling
      // (Pro+; throws the standard 402 plan_limit). All-FREE listings publish on any plan —
      // Game Lab free publish keeps working. Enforced HERE so no route can skip it.
      const paidPrices = await prisma.price.count({
        where: { listingId: listing.id, active: true, model: { not: 'FREE' } },
      });
      if (paidPrices > 0) await requireSellerEntitlement(accountId);
      data.publishedAt = new Date();
    }
    data.status = patch.status;
  }
  if (patch.anmDomain !== undefined) {
    data.anmDomainId = patch.anmDomain == null || patch.anmDomain === ''
      ? null
      : (await resolveOwnedDomain(accountId, patch.anmDomain)).id;
  }
  return prisma.listing.update({ where: { id: listing.id }, data });
}

async function resolveOwnedDomain(accountId: string, label: string) {
  const name = label.trim().toLowerCase().replace(/\.anm$/, '');
  const dom = await prisma.anmDomain.findUnique({ where: { name }, select: { id: true, ownerId: true, status: true } });
  if (!dom || dom.status !== 'ACTIVE') throw new AppStoreError('domain_not_found', `.anm domain not found or inactive: ${name}`, 404);
  if (dom.ownerId !== accountId) throw new AppStoreError('domain_not_yours', 'that .anm domain belongs to someone else', 403);
  return dom;
}

// ── Build file lifecycle ─────────────────────────────────────────────────────

// Stream a raw request body into the builds dir under a temp `incoming-*` name.
// Returns the temp path + streamed size/sha3 (sha3-256 hashed mid-stream).
// Enforces the disk floor BEFORE accepting bytes.
export async function storeIncomingApk(
  body: ReadableStream<Uint8Array> | null,
  maxBytes = APK_MAX_BYTES,
): Promise<{ tmpPath: string; bytes: number; sha3: string }> {
  if ((await freeDiskBytes()) < STORE_MIN_FREE_BYTES + maxBytes) {
    throw new AppStoreError('storage_full', 'store disk is momentarily full — try again soon', 507);
  }
  const tmpId = 'incoming-' + randomBytes(14).toString('base64url').replace(/[^a-zA-Z0-9]/g, '').slice(0, 20) + Date.now().toString(36);
  const tmpPath = buildPath(tmpId);
  const stored = await streamToFile(body, tmpPath, maxBytes);
  return { tmpPath, ...stored };
}

// Move a verified incoming APK to its durable AppBuild path. Returns the final path.
export async function finalizeBuildFile(tmpPath: string, buildId: string): Promise<string> {
  const dest = buildPath(buildId);
  await rename(safeStorePath(tmpPath), safeStorePath(dest));
  return dest;
}

// Remove a builds-store file (failed upload cleanup / build row deletion).
export async function removeBuildFile(path: string | null | undefined): Promise<void> {
  if (!path) return;
  try { await unlink(safeStorePath(path)); } catch { /* already gone */ }
}

// ── Register an uploaded + verified build ────────────────────────────────────

export interface RegisterBuildInput {
  listingId: string;
  actorAccountId: string;
  staged: { tmpPath: string; bytes: number; sha3: string }; // from storeIncomingApk
  verdict: ApkVerdict; // from verifyApk(staged.tmpPath)
  channel?: string;
  releaseNotes?: string | null;
}

export interface RegisterBuildResult {
  build: { id: string; status: string; versionCode: number; versionName: string; channel: string; sha3: string; certSha256: string; reviewNote: string | null };
  checksFailed: boolean;
}

// The single write-path for AppBuild rows. On success the staged file is renamed to its
// durable per-build path; on ANY failure (thrown or CHECKS_FAILED) the staged file is
// deleted — the builds dir only ever holds bytes an AppBuild row points at.
export async function registerBuild(inp: RegisterBuildInput): Promise<RegisterBuildResult> {
  const { staged, verdict } = inp;
  try {
    const listing = await prisma.listing.findUnique({
      where: { id: inp.listingId },
      select: { id: true, ownerId: true, type: true, packageName: true, pinnedCertSha256: true },
    });
    if (!listing) throw new AppStoreError('not_found', 'listing not found', 404);
    if (listing.type !== 'APP') throw new AppStoreError('not_an_app', 'APK builds only apply to APP listings', 409);
    if (listing.ownerId !== inp.actorAccountId) throw new AppStoreError('forbidden', 'not your listing', 403);

    const channel = (inp.channel ?? 'stable').trim().toLowerCase() || 'stable';
    if (!BUILD_CHANNEL_RE.test(channel)) throw new AppStoreError('bad_channel', 'channel must match [a-z][a-z0-9-]{0,15}');
    const releaseNotes = (inp.releaseNotes ?? '').slice(0, 4000) || null;

    // Structural facts we cannot record a row without.
    const pkg = verdict.packageName;
    const versionCode = verdict.versionCode;
    if (!pkg || !isValidPackageName(pkg) || !versionCode || !Number.isSafeInteger(versionCode) || versionCode <= 0) {
      throw new AppStoreError('apk_invalid', `unreadable APK: ${verdict.reasons.join('; ').slice(0, 400) || 'no packageName/versionCode'}`, 422);
    }

    // packageName claim: first build claims it (globally unique); later builds must match.
    if (listing.packageName && listing.packageName !== pkg) {
      throw new AppStoreError('package_mismatch', `APK is ${pkg} but this listing is ${listing.packageName}`, 409);
    }
    if (!listing.packageName) {
      const taken = await prisma.listing.findUnique({ where: { packageName: pkg }, select: { id: true } });
      if (taken && taken.id !== listing.id) throw new AppStoreError('package_taken', 'packageName already claimed by another listing', 409);
    }

    // versionCode strictly monotonic per (listing, channel) over rows that ever counted
    // (CHECKS_FAILED rows are replaceable so a fixed re-upload can reuse its number).
    const agg = await prisma.appBuild.aggregate({
      where: { listingId: listing.id, channel, status: { not: 'CHECKS_FAILED' } },
      _max: { versionCode: true },
    });
    if (!versionCodeAllowed(agg._max.versionCode, versionCode)) {
      throw new AppStoreError('version_not_monotonic', `versionCode ${versionCode} must be greater than ${agg._max.versionCode} on channel "${channel}"`, 409);
    }

    // Per-publisher byte quota across every stored build of every listing they own.
    const used = await prisma.appBuild.aggregate({
      where: { listing: { ownerId: listing.ownerId }, status: { not: 'CHECKS_FAILED' } },
      _sum: { sizeBytes: true },
    });
    if (!quotaAllows(used._sum.sizeBytes ?? 0n, staged.bytes)) {
      throw new AppStoreError('quota_exceeded', `publisher build storage quota exceeded (${PUBLISHER_QUOTA_BYTES} bytes) — delete old builds first`, 413);
    }

    // Signature policy: intrinsic verdict + cert continuity against the listing pin.
    const reasons = [...verdict.reasons];
    if (verdict.certSha256 && !certContinuityOk(listing.pinnedCertSha256, verdict.certSha256)) {
      reasons.push(`signer cert does not match the listing's pinned certificate (pinned ${listing.pinnedCertSha256!.slice(0, 16)}…, got ${verdict.certSha256.slice(0, 16)}…) — cert rotation requires admin action`);
    }
    const badgingJson = {
      packageName: pkg,
      versionCode,
      versionName: verdict.versionName ?? '',
      minSdk: verdict.minSdk ?? 0,
      targetSdk: verdict.targetSdk ?? 0,
      label: verdict.label ?? null,
      permissions: verdict.permissions ?? [],
      sensitivePermissions: verdict.sensitivePermissions ?? [],
      schemes: verdict.schemes ?? null,
      certDn: verdict.certDn ?? null,
      signerCount: verdict.signerCount ?? 0,
    };
    const common = {
      listingId: listing.id,
      versionCode,
      versionName: (verdict.versionName ?? '').slice(0, 100),
      channel,
      packageName: pkg,
      sizeBytes: BigInt(staged.bytes),
      sha3: staged.sha3,
      certSha256: verdict.certSha256 ?? '',
      minSdk: verdict.minSdk ?? 0,
      targetSdk: verdict.targetSdk ?? 0,
      permissionsJson: verdict.permissions ?? [],
      badgingJson,
      releaseNotes,
    };

    // A prior CHECKS_FAILED row at this exact (listing, channel, versionCode) is
    // superseded by this upload — replace it (the unique index would otherwise block).
    const replaceStale = prisma.appBuild.deleteMany({
      where: { listingId: listing.id, channel, versionCode, status: 'CHECKS_FAILED' },
    });

    if (reasons.length > 0) {
      // Record the failure (audit trail of rejected/hostile uploads), keep no bytes.
      const [, build] = await prisma.$transaction([
        replaceStale,
        prisma.appBuild.create({
          data: { ...common, path: '', status: 'CHECKS_FAILED', reviewNote: reasons.join('; ').slice(0, 1000) },
          select: { id: true, status: true, versionCode: true, versionName: true, channel: true, sha3: true, certSha256: true, reviewNote: true },
        }),
      ]);
      await removeBuildFile(staged.tmpPath);
      return { build, checksFailed: true };
    }

    // Checks passed — decide review status.
    const latestApproved = await prisma.appBuild.findFirst({
      where: { listingId: listing.id, status: 'APPROVED' },
      orderBy: { versionCode: 'desc' },
      select: { permissionsJson: true },
    });
    const status = decideBuildStatus({
      hasApprovedBuild: !!latestApproved,
      sensitivePermissions: verdict.sensitivePermissions ?? [],
      approvedPermissions: Array.isArray(latestApproved?.permissionsJson) ? (latestApproved!.permissionsJson as string[]) : null,
    });

    // Row + listing claim/pin atomically; the file rename follows (see cleanup below).
    const [, build] = await prisma.$transaction([
      replaceStale,
      prisma.appBuild.create({
        data: {
          ...common,
          path: '', // set post-rename; '' never resolves to a servable file
          status,
          approvedAt: status === 'APPROVED' ? new Date() : null,
        },
        select: { id: true, status: true, versionCode: true, versionName: true, channel: true, sha3: true, certSha256: true, reviewNote: true },
      }),
      prisma.listing.update({
        where: { id: listing.id },
        data: {
          ...(listing.packageName ? {} : { packageName: pkg }),
          // First accepted build PINS the signer cert (Play-style continuity).
          ...(listing.pinnedCertSha256 ? {} : { pinnedCertSha256: verdict.certSha256! }),
        },
      }),
    ]);
    try {
      const finalPath = await finalizeBuildFile(staged.tmpPath, build.id);
      await prisma.appBuild.update({ where: { id: build.id }, data: { path: finalPath } });
    } catch (e) {
      // The bytes never landed — a row pointing nowhere must not survive, and neither
      // may an already-renamed file the row no longer points at.
      await prisma.appBuild.delete({ where: { id: build.id } }).catch(() => {});
      await removeBuildFile(buildPath(build.id));
      throw e;
    }
    return { build, checksFailed: false };
  } catch (e) {
    await removeBuildFile(staged.tmpPath);
    throw e;
  }
}

// ── Reads used by the routes / workers ───────────────────────────────────────

// Latest APPROVED build for a listing (optionally per channel) — catalog + update-check.
export async function latestApprovedBuild(listingId: string, channel = 'stable') {
  return prisma.appBuild.findFirst({
    where: { listingId, channel, status: 'APPROVED' },
    orderBy: { versionCode: 'desc' },
  });
}

// Publisher storage currently used (bytes across all owned listings' stored builds).
export async function publisherBytesUsed(ownerId: string): Promise<bigint> {
  const used = await prisma.appBuild.aggregate({
    where: { listing: { ownerId }, status: { not: 'CHECKS_FAILED' } },
    _sum: { sizeBytes: true },
  });
  return used._sum.sizeBytes ?? 0n;
}

// Delete a build row AND its file (owner tidy-up / admin takedown). Refuses to delete
// the only APPROVED build of a PUBLISHED listing — the store page must stay installable.
export async function deleteBuild(actorAccountId: string, buildId: string, opts: { admin?: boolean } = {}) {
  const build = await prisma.appBuild.findUnique({
    where: { id: buildId },
    select: { id: true, path: true, status: true, listingId: true, listing: { select: { ownerId: true, status: true } } },
  });
  if (!build) throw new AppStoreError('not_found', 'build not found', 404);
  if (!opts.admin && build.listing.ownerId !== actorAccountId) throw new AppStoreError('forbidden', 'not your build', 403);
  if (build.status === 'APPROVED' && build.listing.status === 'PUBLISHED') {
    const others = await prisma.appBuild.count({ where: { listingId: build.listingId, status: 'APPROVED', id: { not: build.id } } });
    if (others === 0) throw new AppStoreError('last_build', 'cannot delete the only APPROVED build of a published listing', 409);
  }
  await prisma.appBuild.delete({ where: { id: build.id } });
  await removeBuildFile(build.path);
  return { deleted: true };
}
