// End-to-end proof of the Animica Name Service (.anm) using REAL ml_dsa_65 keypairs.
// Exercises: register a .anm domain with a names-scoped agent key, fee debit, resolve, search
// index, records update, renew, transfer, "my names", and the on-chain anchor Merkle root.
import { ml_dsa65 } from '@noble/post-quantum/ml-dsa';
import { sha3_256 } from '@noble/hashes/sha3';

const BASE = 'http://127.0.0.1:4950/api/mkt/v1';
const ADMIN = process.env.MKT_ADMIN_TOKEN || process.env.ADMIN_TOKEN || process.argv[2];
const CHARSET='qpzry9x8gf2tvdw0s3jn54khce6mua7l';
const BECH32M=0x2bc830a3;
function polymod(v){let c=1;const G=[0x3b6a57b2,0x26508e6d,0x1ea119fa,0x3d4233dd,0x2a1462b3];for(const x of v){const t=c>>>25;c=((c&0x1ffffff)<<5)^x;for(let i=0;i<5;i++)if((t>>i)&1)c^=G[i];}return c>>>0;}
function hrpExpand(h){const o=[];for(let i=0;i<h.length;i++)o.push(h.charCodeAt(i)>>5);o.push(0);for(let i=0;i<h.length;i++)o.push(h.charCodeAt(i)&31);return o;}
function checksum(h,d){const v=hrpExpand(h).concat(d).concat([0,0,0,0,0,0]);const m=polymod(v)^BECH32M;const o=[];for(let i=0;i<6;i++)o.push((m>>(5*(5-i)))&31);return o;}
function convertBits(data,from,to,pad){let acc=0,bits=0;const o=[];const max=(1<<to)-1;for(const val of data){acc=(acc<<from)|val;bits+=from;while(bits>=to){bits-=to;o.push((acc>>bits)&max);}}if(pad&&bits>0)o.push((acc<<(to-bits))&max);return o;}
function encodeAddr(payload){const d=convertBits([...payload],8,5,true);const cs=checksum('anim',d);let s='anim1';for(const x of d.concat(cs))s+=CHARSET[x];return s;}
function makeWallet(){
  const kp = ml_dsa65.keygen();
  const alg = new Uint8Array([0x10,0x03]);
  const digest = sha3_256(kp.publicKey);
  const payload = new Uint8Array(34); payload.set(alg,0); payload.set(digest,2);
  const address = encodeAddr(payload);
  return { kp, address, pubHex: Buffer.from(kp.publicKey).toString('hex') };
}
function signMsg(kp, message){
  const bytes = new TextEncoder().encode('animica:signMessage:'+message);
  return Buffer.from(ml_dsa65.sign(kp.secretKey, bytes)).toString('hex');
}
async function j(method, path, body, headers={}){
  const res = await fetch(BASE+path, { method, headers:{'content-type':'application/json',...headers}, body: body?JSON.stringify(body):undefined });
  const data = await res.json().catch(()=>({}));
  return { status: res.status, data };
}
async function register(w, scopes){
  const ch = await j('GET', `/auth/challenge?address=${w.address}`);
  const signature = signMsg(w.kp, ch.data.challenge);
  const reg = await j('POST','/accounts/register',{ address:w.address, challenge:ch.data.challenge, signature, publicKey:w.pubHex, isAgent:true, scopes });
  if(reg.status!==200) throw new Error('register: '+JSON.stringify(reg.data));
  return reg.data.apiKey;
}
const auth = (key)=>({authorization:`Bearer ${key}`});
function assert(cond,msg){ if(!cond){ console.error('  ✗ FAIL:',msg); process.exitCode=1;} else console.log('  ✓',msg); }

(async ()=>{
  console.log('\n=== Animica Name Service (.anm) — end-to-end ===\n');
  const agent = makeWallet();       // an autonomous agent claiming its identity
  const buyer2 = makeWallet();      // transfer recipient
  const rnd = agent.pubHex.slice(0, 3); // 3 hex chars from the key -> keeps name at 7 chars
  const NAME = `nova${rnd}`;        // 7 chars -> 25 ANM/yr
  console.log('agent', agent.address.slice(0,24)+'…  name', NAME+'.anm\n');

  console.log('[1] agent logs in with a names-scoped key + gets funded');
  const key = await register(agent, ['read','use','names','publish','message']);
  const key2 = await register(buyer2, ['read','names']);
  assert(key?.startsWith('anm_mkt_'),'agent got a names-scoped API key');
  await j('POST','/admin/grant',{address:agent.address, amountAnm:'200'},{'x-admin-token':ADMIN});
  const b0 = await j('GET','/balance',null,auth(key));
  assert(b0.data.balanceAnm==='200','agent balance = 200 ANM');

  console.log('\n[2] scope enforcement: a key WITHOUT names cannot register');
  const noNames = makeWallet(); const nnKey = await register(noNames, ['read','use']);
  await j('POST','/admin/grant',{address:noNames.address, amountAnm:'100'},{'x-admin-token':ADMIN});
  const denied = await j('POST','/names',{name:'shouldfail'+rnd, kind:'app'},auth(nnKey));
  assert(denied.status===403,'register without names scope is REJECTED (403)');

  console.log('\n[3] register '+NAME+'.anm (kind=agent) — fee debited, domain created');
  const reg = await j('POST','/names',{name:NAME, kind:'agent', years:1, records:{description:'An autonomous trading agent.', avatar:'🤖'}},auth(key));
  assert(reg.status===201,'domain registered (201)');
  assert(reg.data.domain?.fqdn===`${NAME}.anm`,'fqdn = '+NAME+'.anm');
  assert(reg.data.feeAnm==='25','fee = 25 ANM (6-8 char name)');
  const b1 = await j('GET','/balance',null,auth(key));
  assert(b1.data.balanceAnm==='175','agent balance 200 -> 175 (paid 25)');

  console.log('\n[4] duplicate registration is rejected');
  const dup = await j('POST','/names',{name:NAME, kind:'app'},auth(key2));
  assert(dup.status===409,'second registration of same name is REJECTED (409 taken)');

  console.log('\n[5] resolve '+NAME+'.anm (public, no auth) — the browser/gateway primitive');
  const res = await j('GET',`/names/${NAME}`);
  assert(res.status===200,'resolve returns 200');
  assert(res.data.resolved?.owner===agent.address,'resolver returns the agent owner address');
  assert(res.data.resolved?.records?.description?.includes('trading agent'),'resolver returns records');

  console.log('\n[6] search index finds it (Google-like /names?search=)');
  const s = await j('GET',`/names?search=nova`);
  assert(s.data.results?.some(d=>d.name===NAME),'search "nova" finds '+NAME);
  assert(s.data.total>=1,'index total >= 1');

  console.log('\n[7] update records (attach content CID + endpoint)');
  const upd = await j('PATCH',`/names/${NAME}`,{contentCid:'cid-abc123', records:{description:'Now hosting content.', endpoint:'https://api/agent'}},auth(key));
  assert(upd.status===200,'owner updated records');
  const res2 = await j('GET',`/names/${NAME}`);
  assert(res2.data.resolved?.contentCid==='cid-abc123','contentCid now resolves');

  console.log('\n[8] renew (+1yr) extends expiry, debits fee');
  const before = new Date(res2.data.resolved.expiresAt).getTime();
  const ren = await j('POST',`/names/${NAME}/renew`,{years:1},auth(key));
  assert(ren.status===200,'renew ok');
  assert(new Date(ren.data.domain.expiresAt).getTime()>before,'expiry extended');
  const b2 = await j('GET','/balance',null,auth(key));
  assert(b2.data.balanceAnm==='150','agent balance 175 -> 150 (renew 25)');

  console.log('\n[9] transfer ownership to another wallet');
  const xfer = await j('POST',`/names/${NAME}/transfer`,{toAddress:buyer2.address},auth(key));
  assert(xfer.status===200,'transfer ok');
  const mineOld = await j('GET','/names/mine',null,auth(key));
  const mineNew = await j('GET','/names/mine',null,auth(key2));
  assert(!mineOld.data.domains?.some(d=>d.name===NAME),'old owner no longer lists it');
  assert(mineNew.data.domains?.some(d=>d.name===NAME),'new owner now lists it');

  console.log('\n[10] anchor: Merkle root over the whole registry (on-chain-anchorable)');
  const anchor = await j('GET','/names/anchor');
  assert(anchor.status===200 && /^[0-9a-f]{64}$/.test(anchor.data.merkleRoot||''),'anchor returns a sha3-256 Merkle root');
  assert(anchor.data.domainCount>=1,'anchor covers >=1 active domain');
  console.log('     root='+anchor.data.merkleRoot.slice(0,24)+'…  domains='+anchor.data.domainCount);

  console.log('\n=== ANS e2e complete ===\n');
})().catch(e=>{ console.error('FATAL', e); process.exit(1); });
