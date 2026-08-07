import { spawn } from 'node:child_process';
import { readdir, stat } from 'node:fs/promises';
import { join } from 'node:path';

// App Store — server-side APK verification. Shells out to the Android build tools
// (apksigner for signature/scheme/cert, aapt2 for badging) against the uploaded file.
// FAIL-CLOSED: anything unparseable, unsigned, v1-only, debug-signed or multi-signer
// is a hard verdict — never "assume fine". The parsed cert SHA-256 + badging feed the
// AppBuild row, the catalog, the versions API and the license leaf; the device
// re-verifies both before install, so these values must be exact.
// NOTE keytool cannot parse v2/v3 APK signatures ("Not a signed jar file") — apksigner
// is the only correct tool here.

const TOOL_TIMEOUT_MS = Number(process.env.APK_VERIFY_TIMEOUT_MS ?? 60_000);

// ── Tool discovery ───────────────────────────────────────────────────────────
// Explicit env dir first, then the NEWEST build-tools under the known SDK roots
// (/opt/android-sdk on this box, then $ANDROID_SDK_ROOT / $ANDROID_HOME). Resolved
// once per process; throws only for the operator problem of no SDK at all.

let _toolsDir: string | null = null;

export async function locateBuildToolsDir(): Promise<string> {
  if (_toolsDir) return _toolsDir;
  if (process.env.ANDROID_BUILD_TOOLS_DIR) {
    _toolsDir = process.env.ANDROID_BUILD_TOOLS_DIR;
    return _toolsDir;
  }
  const roots = ['/opt/android-sdk', process.env.ANDROID_SDK_ROOT || '', process.env.ANDROID_HOME || ''].filter(Boolean);
  for (const root of roots) {
    const bt = join(root, 'build-tools');
    let versions: string[] = [];
    try { versions = await readdir(bt); } catch { continue; }
    // Numeric-aware newest-first so 36.0.0 beats 9.0.0 and 28.0.3.
    versions.sort((a, b) => b.localeCompare(a, undefined, { numeric: true }));
    for (const v of versions) {
      try {
        await stat(join(bt, v, 'apksigner'));
        await stat(join(bt, v, 'aapt2'));
        _toolsDir = join(bt, v);
        return _toolsDir;
      } catch { /* incomplete build-tools install — try an older version */ }
    }
  }
  throw new Error('Android build-tools not found (need apksigner + aapt2 under /opt/android-sdk/build-tools or $ANDROID_HOME/build-tools; or set ANDROID_BUILD_TOOLS_DIR)');
}

// Permissions that force a build into PENDING_REVIEW even for a pinned same-signer
// publisher (SMS, accessibility, device-admin + the classic overlay/install vectors).
export const SENSITIVE_PERMISSIONS = new Set([
  'android.permission.SEND_SMS',
  'android.permission.RECEIVE_SMS',
  'android.permission.READ_SMS',
  'android.permission.RECEIVE_MMS',
  'android.permission.RECEIVE_WAP_PUSH',
  'android.permission.BIND_ACCESSIBILITY_SERVICE',
  'android.permission.BIND_DEVICE_ADMIN',
  'android.permission.SYSTEM_ALERT_WINDOW',
  'android.permission.REQUEST_INSTALL_PACKAGES',
  'android.permission.READ_CALL_LOG',
  'android.permission.WRITE_CALL_LOG',
  'android.permission.PROCESS_OUTGOING_CALLS',
  'android.permission.QUERY_ALL_PACKAGES',
  'android.permission.MANAGE_EXTERNAL_STORAGE',
  'android.permission.ACCESS_BACKGROUND_LOCATION',
]);

export interface ApkVerdict {
  ok: boolean; // signature valid + v2/v3 scheme + single signer + not debug-signed
  reasons: string[]; // why !ok (empty when ok)
  // Signature facts (present when apksigner could parse the file at all).
  certSha256?: string;
  certDn?: string;
  signerCount?: number;
  schemes?: { v1: boolean; v2: boolean; v3: boolean };
  // Badging facts (present when aapt2 could parse the file at all).
  packageName?: string;
  versionCode?: number;
  versionName?: string;
  minSdk?: number;
  targetSdk?: number;
  label?: string;
  permissions?: string[];
  sensitivePermissions?: string[];
}

function run(bin: string, args: string[]): Promise<{ code: number | null; out: string; err: string }> {
  return new Promise((resolve) => {
    const child = spawn(bin, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '', err = '';
    const timer = setTimeout(() => { try { child.kill('SIGKILL'); } catch { /* gone */ } }, TOOL_TIMEOUT_MS);
    child.stdout.on('data', (d) => { if (out.length < 1_000_000) out += d.toString(); });
    child.stderr.on('data', (d) => { if (err.length < 100_000) err += d.toString(); });
    child.on('error', (e) => { clearTimeout(timer); resolve({ code: null, out, err: `spawn failed: ${e.message}` }); });
    child.on('close', (code) => { clearTimeout(timer); resolve({ code, out, err }); });
  });
}

// ── Pure parsers (unit-tested against captured tool output — no shelling) ────

// Debug certs are whatever each developer's SDK mints — signing proves nothing and
// EVERY debug keystore carries this CN.
export function isDebugCertDn(dn: string | null | undefined): boolean {
  return !!dn && /(^|[,\s])CN=Android Debug(,|$)/i.test(dn);
}

// Parse `apksigner verify --verbose --print-certs` STDOUT (exit 0) into signature facts
// + policy reasons. FAIL-CLOSED: whatever can't be found becomes a reason.
export function parseApksignerText(text: string): Partial<ApkVerdict> & { reasons: string[] } {
  const reasons: string[] = [];
  const scheme = (v: string) => new RegExp(`Verified using ${v} scheme[^:]*: true`).test(text);
  const schemes = { v1: scheme('v1'), v2: scheme('v2'), v3: /Verified using v3(\.1)? scheme[^:]*: true/.test(text) };
  const signerCount = Number(text.match(/Number of signers: (\d+)/)?.[1] ?? 0);
  const certSha256 = text.match(/Signer #1 certificate SHA-256 digest: ([0-9a-f]{64})/)?.[1];
  const certDn = text.match(/Signer #1 certificate DN: (.+)/)?.[1]?.trim();

  if (!schemes.v2 && !schemes.v3) reasons.push('APK must be signed with signature scheme v2 or v3 (v1/JAR-only signing is rejected)');
  if (signerCount !== 1) reasons.push(`exactly one signer required (found ${signerCount})`);
  if (!certSha256) reasons.push('could not extract signer certificate SHA-256');
  if (isDebugCertDn(certDn)) reasons.push('debug-signed APK rejected (CN=Android Debug) — sign with a release keystore');
  return { certSha256, certDn, signerCount, schemes, reasons };
}

// Parse `aapt2 dump badging` STDOUT (exit 0) into manifest facts + policy reasons.
export function parseBadgingText(text: string): Partial<ApkVerdict> & { reasons: string[] } {
  const reasons: string[] = [];
  const pkg = text.match(/^package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'/m);
  const packageName = pkg?.[1];
  const versionCode = pkg ? Number(pkg[2]) : undefined;
  const versionName = pkg?.[3];
  // aapt2 emits minSdkVersion; older aapt emits sdkVersion — accept either.
  const minSdk = Number(text.match(/^minSdkVersion:'(\d+)'/m)?.[1] ?? text.match(/^sdkVersion:'(\d+)'/m)?.[1] ?? NaN);
  const targetSdk = Number(text.match(/^targetSdkVersion:'(\d+)'/m)?.[1] ?? NaN);
  const label = text.match(/^application-label:'([^']*)'/m)?.[1];
  const permissions = [...new Set(
    [...text.matchAll(/^uses-permission(?:-sdk-23)?: name='([^']+)'/gm)].map((m) => m[1]),
  )];
  const sensitivePermissions = permissions.filter((p) => SENSITIVE_PERMISSIONS.has(p));

  if (!packageName || !/^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$/.test(packageName)) {
    reasons.push(`invalid or missing packageName${packageName ? `: ${packageName.slice(0, 100)}` : ''}`);
  }
  if (!Number.isInteger(versionCode) || (versionCode as number) <= 0) reasons.push('invalid or missing versionCode');
  if (!Number.isInteger(minSdk)) reasons.push('missing minSdkVersion');
  return {
    packageName, versionCode, versionName: versionName ?? '',
    minSdk: Number.isInteger(minSdk) ? minSdk : undefined,
    targetSdk: Number.isInteger(targetSdk) ? targetSdk : (Number.isInteger(minSdk) ? minSdk : undefined),
    label, permissions, sensitivePermissions, reasons,
  };
}

// ── Tool wrappers ────────────────────────────────────────────────────────────

// `apksigner verify --verbose --print-certs` — signature validity, schemes, signer cert.
async function verifySignature(path: string): Promise<Partial<ApkVerdict> & { reasons: string[] }> {
  const dir = await locateBuildToolsDir();
  const r = await run(join(dir, 'apksigner'), ['verify', '--verbose', '--print-certs', path]);
  if (r.code !== 0) {
    const detail = (r.err || r.out).trim().split('\n')[0]?.slice(0, 300) || `exit ${r.code}`;
    return { reasons: [`signature verification failed: ${detail}`] };
  }
  return parseApksignerText(r.out);
}

// `aapt2 dump badging` — packageName, versionCode/Name, min/target SDK, permissions, label.
async function readBadging(path: string): Promise<Partial<ApkVerdict> & { reasons: string[] }> {
  const dir = await locateBuildToolsDir();
  const r = await run(join(dir, 'aapt2'), ['dump', 'badging', path]);
  if (r.code !== 0) {
    const detail = (r.err || r.out).trim().split('\n')[0]?.slice(0, 300) || `exit ${r.code}`;
    return { reasons: [`badging parse failed (not a valid APK?): ${detail}`] };
  }
  return parseBadgingText(r.out);
}

// Full verdict over an uploaded APK on disk. `ok` covers only intrinsic file checks;
// listing-level policy (packageName claim, cert pin, versionCode monotonicity) lives in
// lib/appStore.ts registerBuild — it has the listing row.
export async function verifyApk(path: string): Promise<ApkVerdict> {
  const [sig, badging] = [await verifySignature(path), await readBadging(path)];
  const reasons = [...sig.reasons, ...badging.reasons];
  return {
    ok: reasons.length === 0,
    reasons,
    certSha256: sig.certSha256,
    certDn: sig.certDn,
    signerCount: sig.signerCount,
    schemes: sig.schemes,
    packageName: badging.packageName,
    versionCode: badging.versionCode,
    versionName: badging.versionName,
    minSdk: badging.minSdk,
    targetSdk: badging.targetSdk,
    label: badging.label,
    permissions: badging.permissions,
    sensitivePermissions: badging.sensitivePermissions,
  };
}
