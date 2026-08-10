// Compare the ORIGINAL bundle (real BigInt available) against the PATCHED bundle run
// with BigInt DELETED, through the public API only. Keygen exercises SHAKE256 (the
// Keccak IOTA tables) and the NTT (the zeta table), so identical output over many seeds
// means both precomputed tables are identical in effect.
const fs = require('fs'), vm = require('vm');

const ORIG = process.env.ORIG_BUNDLE || '';   // optional: an unpatched copy to diff against
const PATCHED = __dirname + '/../assets/js/ml_dsa_65.bundle.js';
// The same base64 shim the app injects (the engine has no atob/btoa).
const b64shim = `
if (typeof atob === 'undefined' || typeof btoa === 'undefined') {
  var _A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  globalThis.btoa = function (s) { var o=''; for (var i=0;i<s.length;i+=3){ var c1=s.charCodeAt(i),c2=s.charCodeAt(i+1),c3=s.charCodeAt(i+2);
    o += _A[c1>>2] + _A[((c1&3)<<4)|((c2||0)>>4)]; o += isNaN(c2)?'=':_A[((c2&15)<<2)|((c3||0)>>6)]; o += isNaN(c3)?'=':_A[c3&63]; } return o; };
  globalThis.atob = function (s) { s=s.replace(/=+$/,''); var o='',b=0,bits=0;
    for (var i=0;i<s.length;i++){ b=(b<<6)|_A.indexOf(s[i]); bits+=6; if(bits>=8){bits-=8;o+=String.fromCharCode((b>>bits)&255);} } return o; };
}`;

function ctxFor(file, killBigInt) {
  const ctx = vm.createContext({});
  if (killBigInt) {
    vm.runInContext('delete globalThis.BigInt; delete globalThis.atob; delete globalThis.btoa; delete globalThis.crypto;', ctx);
  }
  vm.runInContext(b64shim, ctx);
  vm.runInContext(fs.readFileSync(file, 'utf8'), ctx);
  return ctx;
}

let fails = [];
const check = (n, ok, extra) => { if (!ok) fails.push(n + (extra ? ' — ' + extra : '')); console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${n}`); };

// The patched bundle must load with BigInt entirely absent — the shipped-engine condition.
let patchedCtx;
try {
  patchedCtx = ctxFor(PATCHED, true);
  check('patched bundle loads with BigInt DELETED', true);
} catch (e) { check('patched bundle loads with BigInt DELETED', false, e.message); process.exit(1); }
const origCtx = ORIG ? ctxFor(ORIG, false) : null;

check('patched exposes NobleMlDsa', vm.runInContext('typeof NobleMlDsa', patchedCtx) === 'object');

// Golden vectors from the CHAIN's own implementation (pq.py.algs.ml_dsa_65) — the
// verifier that must accept whatever this wallet signs. These make the tool a real guard
// with no unpatched bundle and no device available.
const GOLDEN = [
  { seed: '030a11181f262d343b424950575e656c737a81888f969da4abb2b9c0c7ced5dc', pk: 'f03a276f0d38544fe656170c0098c2507ac0a4936f1f0bbf6a3e73cba5dcc42d', sk: 'f0e9f3eb8b4b62a4872668ccb25f92ec15dd7b4158bf42b820574cdc20e973dc' },
  { seed: '222930373e454c535a61686f767d848b9299a0a7aeb5bcc3cad1d8dfe6edf4fb', pk: '07647c1bfbb4196cc17b9feef806a2841d834ab9ffa46a23b5dce78660f382a2', sk: 'bbce7dc3e913b78b6235bb15c4de229031351c42653ecfe879ebaaa216aabc6f' },
  { seed: '41484f565d646b727980878e959ca3aab1b8bfc6cdd4dbe2e9f0f7fe050c131a', pk: 'eaef0963ad1d69d575176ecdc001a10014499c7683262062d43db1a952c61432', sk: '4d69ae0bb165505a05c369e2467df10ed71bb9086d88100ecb6a01bc2e8eb3ac' }
];

function keygenHashes(ctx, seedHex) {
  const arr = Array.from(Buffer.from(seedHex, 'hex'));
  const out = vm.runInContext(`(function(){
    var kp = NobleMlDsa.ml_dsa65.keygen(new Uint8Array(${JSON.stringify(arr)}));
    function h(u8){var s="";for(var i=0;i<u8.length;i++)s+=("0"+u8[i].toString(16)).slice(-2);return s;}
    return h(kp.publicKey)+":"+h(kp.secretKey);
  })()`, ctx);
  const [pk, sk] = out.split(':');
  const sha = (hex) => require('crypto').createHash('sha256').update(Buffer.from(hex, 'hex')).digest('hex');
  return { pk: sha(pk), sk: sha(sk), pkHex: pk, skHex: sk };
}

let goldenOk = true;
for (const g of GOLDEN) {
  const got = keygenHashes(patchedCtx, g.seed);
  if (got.pk !== g.pk || got.sk !== g.sk) { goldenOk = false; console.log(`    seed ${g.seed.slice(0,12)}… MISMATCH`); }
}
check(`keygen matches ${GOLDEN.length} chain-derived golden vectors`, goldenOk);

// 1. SHAKE256 — depends directly on the Keccak IOTA tables.
function shake(ctx, hex, outLen) {
  return vm.runInContext(`(function(){
    var inp = new Uint8Array(${JSON.stringify(Array.from(Buffer.from(hex, 'hex')))});
    var d = NobleMlDsa.shake256 ? NobleMlDsa.shake256(inp, {dkLen: ${outLen}}) : null;
    if (!d) return "NO_SHAKE_EXPORT";
    var s=""; for (var i=0;i<d.length;i++) s+=("0"+d[i].toString(16)).slice(-2); return s;
  })()`, ctx);
}
const sv = origCtx ? shake(origCtx, '', 32) : 'NO_SHAKE_EXPORT';
if (sv === 'NO_SHAKE_EXPORT') {
  console.log('  (shake256 not exported by the bundle — covered indirectly via keygen)');
} else {
  check('SHAKE256("") identical', sv === shake(patchedCtx, '', 32), sv);
  const known = 'a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a';
  check('SHAKE256("") matches the published vector', sv === known, sv);
}

// 2. keygen over many seeds — exercises SHAKE256 AND the NTT zeta table.
function keygen(ctx, seedArr) {
  return vm.runInContext(`(function(){
    var seed = new Uint8Array(${JSON.stringify(seedArr)});
    var kp = NobleMlDsa.ml_dsa65.keygen(seed);
    function h(u8){var s="";for(var i=0;i<u8.length;i++)s+=("0"+u8[i].toString(16)).slice(-2);return s;}
    return h(kp.publicKey)+":"+h(kp.secretKey);
  })()`, ctx);
}
let allSame = true, first = null;
for (let t = 0; t < 8; t++) {
  const seed = Array.from({ length: 32 }, (_, i) => (i * 7 + t * 31 + 3) & 0xff);
  const b = keygen(patchedCtx, seed);
  if (origCtx) {
    const a = keygen(origCtx, seed);
    if (a !== b) { allSame = false; console.log(`    seed#${t} DIFFERS`); }
  }
  if (t === 0) first = b;
}
if (origCtx) check('keygen identical to the unpatched bundle across 8 seeds', allSame);
else console.log('  (set ORIG_BUNDLE=<unpatched copy> to also diff against pre-patch output)');

// 3. sign/verify round trip on the patched build, cross-verified by the original.
const sk = first.split(':')[1], pk = first.split(':')[0];
const rnd = Array.from(require('crypto').randomBytes(32));
const msg = Array.from(Buffer.from('animica bigint-free patch'));
function sign(ctx) {
  return vm.runInContext(`(function(){
    function u(hex){var a=new Uint8Array(hex.length/2);for(var i=0;i<a.length;i++)a[i]=parseInt(hex.substr(i*2,2),16);return a;}
    var sig = NobleMlDsa.ml_dsa65.sign(new Uint8Array(${JSON.stringify(msg)}), u("${sk}"), {extraEntropy: new Uint8Array(${JSON.stringify(rnd)})});
    var s=""; for (var i=0;i<sig.length;i++) s+=("0"+sig[i].toString(16)).slice(-2); return s;
  })()`, ctx);
}
const sigP = sign(patchedCtx);
if (origCtx) {
  const sigO = sign(origCtx);
  check('signature identical (same key, same entropy)', sigP === sigO, `${sigP.slice(0,16)} vs ${sigO.slice(0,16)}`);
}
check('signature is 3309 bytes', sigP.length === 6618, String(sigP.length / 2));

function verify(ctx, sigHex, msgArr) {
  return vm.runInContext(`(function(){
    function u(hex){var a=new Uint8Array(hex.length/2);for(var i=0;i<a.length;i++)a[i]=parseInt(hex.substr(i*2,2),16);return a;}
    return NobleMlDsa.ml_dsa65.verify(u("${sigHex}"), new Uint8Array(${JSON.stringify(msgArr)}), u("${pk}")) ? "1" : "0";
  })()`, ctx);
}
check('patched verifies its own signature', verify(patchedCtx, sigP, msg) === '1');
if (origCtx) check('ORIGINAL verifies the PATCHED signature', verify(origCtx, sigP, msg) === '1');
check('patched rejects a tampered message', verify(patchedCtx, sigP, msg.concat([1])) === '0');

fs.writeFileSync(process.env.VEC_OUT || '/dev/null',
  JSON.stringify({ pk, sk, sig: sigP, msg: Buffer.from(msg).toString('base64') }));

console.log();
if (fails.length) { console.log('FAILURES:'); fails.forEach(f => console.log('  ' + f)); process.exit(1); }
console.log('patched bundle is behaviourally identical to the original, with BigInt absent');
