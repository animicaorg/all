/**
 * App-store lib self-check: unit-tests the PURE policy/parsing functions of
 * lib/apkVerify.ts + lib/appStore.ts (fixtures, no DB, no network), then runs the real
 * apksigner/aapt2 pipeline against an actual APK and asserts the debug-cert reject path
 * fires (the shipped wallet APK is debug-signed — the perfect negative fixture).
 *
 *   npx tsx scripts/store_selfcheck.ts [path/to.apk]
 *
 * Exit 0 = all checks pass. No service, no DB row, no file is touched.
 */
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import {
  isDebugCertDn, parseApksignerText, parseBadgingText, verifyApk, SENSITIVE_PERMISSIONS,
} from '../lib/apkVerify';
import {
  certContinuityOk, decideBuildStatus, isValidPackageName, normalizeAppCategory,
  quotaAllows, versionCodeAllowed, BUILD_CHANNEL_RE,
} from '../lib/appStore';

let passed = 0;
function check(name: string, fn: () => void) {
  try {
    fn();
    passed++;
    console.log(`  ok   ${name}`);
  } catch (e) {
    console.error(`  FAIL ${name}: ${(e as Error).message}`);
    process.exitCode = 1;
  }
}

// ── apkVerify pure parsers ───────────────────────────────────────────────────
console.log('apkVerify parsers');

const APKSIGNER_DEBUG_FIXTURE = `Verifies
Verified using v1 scheme (JAR signing): false
Verified using v2 scheme (APK Signature Scheme v2): true
Verified using v3 scheme (APK Signature Scheme v3): false
Verified using v3.1 scheme (APK Signature Scheme v3.1): false
Verified using v4 scheme (APK Signature Scheme v4): false
Verified for SourceStamp: false
Number of signers: 1
Signer #1 certificate DN: C=US, O=Android, CN=Android Debug
Signer #1 certificate SHA-256 digest: 1e4d881ed60506e818546ea45c93cd651f0d5255b9193eef0f5ad8a466ac7f65
Signer #1 certificate SHA-1 digest: 37e365be5dec41970a7a529fdcd3866fcf34ea4b
Signer #1 certificate MD5 digest: 3f6d4864f7ee74e865c2a67d0d8fc8d6`;

check('parseApksignerText extracts cert + schemes, rejects the debug DN', () => {
  const r = parseApksignerText(APKSIGNER_DEBUG_FIXTURE);
  assert.equal(r.certSha256, '1e4d881ed60506e818546ea45c93cd651f0d5255b9193eef0f5ad8a466ac7f65');
  assert.equal(r.signerCount, 1);
  assert.deepEqual(r.schemes, { v1: false, v2: true, v3: false });
  assert.equal(r.reasons.length, 1);
  assert.match(r.reasons[0], /debug-signed/);
});

check('parseApksignerText: release cert on v3 passes clean', () => {
  const releaseFix = APKSIGNER_DEBUG_FIXTURE
    .replace('CN=Android Debug', 'CN=Animica Ltd')
    .replace('v3 scheme (APK Signature Scheme v3): false', 'v3 scheme (APK Signature Scheme v3): true');
  assert.deepEqual(parseApksignerText(releaseFix).reasons, []);
});

check('parseApksignerText: v1-only signing rejected', () => {
  const v1only = APKSIGNER_DEBUG_FIXTURE
    .replace('CN=Android Debug', 'CN=Animica Ltd')
    .replace('v1 scheme (JAR signing): false', 'v1 scheme (JAR signing): true')
    .replace('v2 scheme (APK Signature Scheme v2): true', 'v2 scheme (APK Signature Scheme v2): false');
  assert.match(parseApksignerText(v1only).reasons.join(' '), /v2 or v3/);
});

check('parseApksignerText: multi-signer rejected', () => {
  const multi = APKSIGNER_DEBUG_FIXTURE
    .replace('CN=Android Debug', 'CN=Animica Ltd')
    .replace('Number of signers: 1', 'Number of signers: 2');
  assert.match(parseApksignerText(multi).reasons.join(' '), /exactly one signer/);
});

check('isDebugCertDn matches only the debug CN', () => {
  assert.equal(isDebugCertDn('C=US, O=Android, CN=Android Debug'), true);
  assert.equal(isDebugCertDn('CN=Android Debugger, O=Legit'), false);
  assert.equal(isDebugCertDn('CN=Animica Ltd'), false);
  assert.equal(isDebugCertDn(null), false);
});

const BADGING_FIXTURE = `package: name='org.animica.animica_wallet' versionCode='4' versionName='0.1.3' platformBuildVersionName='16' platformBuildVersionCode='36' compileSdkVersion='36' compileSdkVersionCodename='16'
minSdkVersion:'24'
targetSdkVersion:'36'
uses-permission: name='android.permission.CAMERA'
uses-permission: name='android.permission.INTERNET'
uses-permission: name='android.permission.INTERNET'
uses-permission-sdk-23: name='android.permission.SEND_SMS'
application-label:'animica_wallet'`;

check('parseBadgingText extracts package facts + dedupes + flags sensitive perms', () => {
  const b = parseBadgingText(BADGING_FIXTURE);
  assert.deepEqual(b.reasons, []);
  assert.equal(b.packageName, 'org.animica.animica_wallet');
  assert.equal(b.versionCode, 4);
  assert.equal(b.versionName, '0.1.3');
  assert.equal(b.minSdk, 24);
  assert.equal(b.targetSdk, 36);
  assert.equal(b.label, 'animica_wallet');
  assert.deepEqual(b.permissions, ['android.permission.CAMERA', 'android.permission.INTERNET', 'android.permission.SEND_SMS']);
  assert.deepEqual(b.sensitivePermissions, ['android.permission.SEND_SMS']);
});

check('parseBadgingText fails closed on garbage', () => {
  const b = parseBadgingText('this is not badging output');
  assert.ok(b.reasons.length >= 2);
  assert.equal(b.packageName, undefined);
});

check('sensitive-permission set covers the design core (SMS, accessibility, device-admin)', () => {
  for (const p of ['android.permission.SEND_SMS', 'android.permission.BIND_ACCESSIBILITY_SERVICE', 'android.permission.BIND_DEVICE_ADMIN']) {
    assert.ok(SENSITIVE_PERMISSIONS.has(p), p);
  }
});

// ── appStore pure policy ─────────────────────────────────────────────────────
console.log('appStore policy');

check('versionCodeAllowed: strictly monotonic per channel', () => {
  assert.equal(versionCodeAllowed(null, 1), true); // first build
  assert.equal(versionCodeAllowed(4, 5), true);
  assert.equal(versionCodeAllowed(4, 4), false); // replay
  assert.equal(versionCodeAllowed(4, 3), false); // downgrade
  assert.equal(versionCodeAllowed(null, 0), false);
  assert.equal(versionCodeAllowed(null, 1.5), false);
});

check('certContinuityOk: first build pins, later builds must match', () => {
  assert.equal(certContinuityOk(null, 'aa'.repeat(32)), true); // pin now
  assert.equal(certContinuityOk('aa'.repeat(32), 'aa'.repeat(32)), true);
  assert.equal(certContinuityOk('aa'.repeat(32), 'bb'.repeat(32)), false); // hijack attempt
});

check('decideBuildStatus: first build => manual review', () => {
  assert.equal(decideBuildStatus({ hasApprovedBuild: false, sensitivePermissions: [], approvedPermissions: null }), 'PENDING_REVIEW');
});

check('decideBuildStatus: same-signer update auto-approves', () => {
  assert.equal(decideBuildStatus({ hasApprovedBuild: true, sensitivePermissions: [], approvedPermissions: ['android.permission.INTERNET'] }), 'APPROVED');
});

check('decideBuildStatus: newly-added sensitive permission => back to review', () => {
  assert.equal(decideBuildStatus({
    hasApprovedBuild: true,
    sensitivePermissions: ['android.permission.SEND_SMS'],
    approvedPermissions: ['android.permission.INTERNET'],
  }), 'PENDING_REVIEW');
});

check('decideBuildStatus: already-approved sensitive permission stays auto', () => {
  assert.equal(decideBuildStatus({
    hasApprovedBuild: true,
    sensitivePermissions: ['android.permission.SEND_SMS'],
    approvedPermissions: ['android.permission.SEND_SMS', 'android.permission.INTERNET'],
  }), 'APPROVED');
});

check('quotaAllows: publisher byte quota', () => {
  assert.equal(quotaAllows(0n, 100, 1000n), true);
  assert.equal(quotaAllows(900n, 100, 1000n), true); // exactly at quota
  assert.equal(quotaAllows(901n, 100, 1000n), false);
});

check('isValidPackageName: Android applicationId shape', () => {
  assert.equal(isValidPackageName('org.animica.animica_wallet'), true);
  assert.equal(isValidPackageName('a.b'), true);
  assert.equal(isValidPackageName('single'), false); // needs 2+ segments
  assert.equal(isValidPackageName('org.1bad'), false); // segment starts with digit
  assert.equal(isValidPackageName('org..double'), false);
  assert.equal(isValidPackageName(''), false);
  assert.equal(isValidPackageName(null), false);
});

check('normalizeAppCategory + channel regex', () => {
  assert.equal(normalizeAppCategory('games'), 'GAMES');
  assert.equal(normalizeAppCategory('AI Agents'), 'AI_AGENTS');
  assert.equal(normalizeAppCategory('dev-apps'), 'DEV_APPS');
  assert.equal(normalizeAppCategory('crypto'), null);
  assert.equal(BUILD_CHANNEL_RE.test('stable'), true);
  assert.equal(BUILD_CHANNEL_RE.test('beta-2'), true);
  assert.equal(BUILD_CHANNEL_RE.test('Bad_Channel'), false);
});

// ── Live pipeline against a real APK ─────────────────────────────────────────
// The shipped wallet APK is debug-signed — verifyApk must extract the real facts AND
// refuse it via the debug-cert path.
const apkPath = process.argv[2] || '/var/www/animica.org/wallet/animica-wallet-android.apk';
(async () => {
  if (!existsSync(apkPath)) {
    console.log(`live check SKIPPED (no APK at ${apkPath})`);
  } else {
    console.log(`live pipeline (${apkPath})`);
    const v = await verifyApk(apkPath);
    check('verifyApk extracts real signer cert + badging', () => {
      assert.equal(v.packageName, 'org.animica.animica_wallet');
      // versionCode moves with wallet releases — assert shape, not a pinned number.
      assert.ok(Number.isSafeInteger(v.versionCode) && (v.versionCode as number) > 0, `versionCode ${v.versionCode}`);
      assert.equal(v.minSdk, 24);
      assert.match(v.certSha256 ?? '', /^[0-9a-f]{64}$/);
      assert.equal(v.signerCount, 1);
      assert.equal(v.schemes?.v2, true);
    });
    check('verifyApk REJECTS the debug-signed wallet APK', () => {
      assert.equal(v.ok, false);
      assert.match(v.reasons.join(' '), /debug-signed/);
    });
  }
  console.log(process.exitCode ? `SELF-CHECK FAILED (${passed} passed)` : `all ${passed} checks passed`);
})();
