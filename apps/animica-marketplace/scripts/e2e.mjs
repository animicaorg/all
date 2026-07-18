// End-to-end proof of the Animica agent economy using REAL ml_dsa_65 keypairs.
// Exercises: wallet-challenge login + address binding, scoped API keys, agent profiles (AADP),
// listing publish, AI->AI purchase with 80/20 split, and escrowed agent-to-agent task commerce.
import { ml_dsa65 } from '@noble/post-quantum/ml-dsa';
import { sha3_256 } from '@noble/hashes/sha3';

const BASE = 'http://127.0.0.1:4950/api/mkt/v1';
const ADMIN = 'devtest123';

// --- bech32m address encode (mirrors packages/launchpad/shared/src/address.ts) ---
const CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l';
const BECH32M = 0x2bc830a3;
function polymod(v){let c=1;const G=[0x3b6a57b2,0x26508e6d,0x1ea119fa,0x3d4233dd,0x2a1462b3];for(const x of v){const t=c>>>25;c=((c&0x1ffffff)<<5)^x;for(let i=0;i<5;i++)if((t>>i)&1)c^=G[i];}return c>>>0;}
function hrpExpand(h){const o=[];for(let i=0;i<h.length;i++)o.push(h.charCodeAt(i)>>5);o.push(0);for(let i=0;i<h.length;i++)o.push(h.charCodeAt(i)&31);return o;}
function checksum(h,d){const v=hrpExpand(h).concat(d).concat([0,0,0,0,0,0]);const m=polymod(v)^BECH32M;const o=[];for(let i=0;i<6;i++)o.push((m>>(5*(5-i)))&31);return o;}
function convertBits(data,from,to,pad){let acc=0,bits=0;const o=[];const max=(1<<to)-1;for(const val of data){acc=(acc<<from)|val;bits+=from;while(bits>=to){bits-=to;o.push((acc>>bits)&max);}}if(pad&&bits>0)o.push((acc<<(to-bits))&max);return o;}
function encodeAddr(payload){const d=convertBits([...payload],8,5,true);const cs=checksum('anim',d);let s='anim1';for(const x of d.concat(cs))s+=CHARSET[x];return s;}

function makeWallet(){
  const kp = ml_dsa65.keygen();
  const digest = sha3_256(kp.publicKey);
  const payload = new Uint8Array(34);
  payload[0]=0x10; payload[1]=0x03; payload.set(digest,2);
  const address = encodeAddr(payload);
  return { kp, address, pubHex:'0x'+Buffer.from(kp.publicKey).toString('hex') };
}
function signMsg(kp, message){
  const bytes = new TextEncoder().encode('animica:signMessage:'+message);
  const sig = ml_dsa65.sign(kp.secretKey, bytes);
  return '0x'+Buffer.from(sig).toString('hex');
}

async function j(method, path, body, headers={}){
  const res = await fetch(BASE+path, { method, headers:{'content-type':'application/json',...headers}, body: body?JSON.stringify(body):undefined });
  const data = await res.json().catch(()=>({}));
  return { status: res.status, data };
}

async function register(w, scopes){
  const ch = await j('GET', `/auth/challenge?address=${w.address}`);
  if(ch.status!==200) throw new Error('challenge: '+JSON.stringify(ch.data));
  const signature = signMsg(w.kp, ch.data.challenge);
  const reg = await j('POST','/accounts/register',{ address:w.address, challenge:ch.data.challenge, signature, publicKey:w.pubHex, isAgent:true, scopes });
  if(reg.status!==200) throw new Error('register: '+JSON.stringify(reg.data));
  return reg.data.apiKey;
}
const auth = (key)=>({authorization:`Bearer ${key}`});

function assert(cond,msg){ if(!cond){ console.error('  ✗ FAIL:',msg); process.exitCode=1;} else console.log('  ✓',msg); }

(async ()=>{
  console.log('\n=== Animica Agent Economy — end-to-end ===\n');

  const alice = makeWallet(); // hirer + buyer
  const bob = makeWallet();   // worker + seller
  console.log('alice', alice.address.slice(0,24)+'…');
  console.log('bob  ', bob.address.slice(0,24)+'…\n');

  console.log('[1] wallet-challenge login + register (real ml_dsa_65 sign/verify + address binding)');
  const aliceKey = await register(alice, ['read','use','buy','publish','message','withdraw','names']);
  const bobKey   = await register(bob,   ['read','use','buy','publish','message','withdraw','names']);
  assert(aliceKey?.startsWith('anm_mkt_'),'alice got scoped API key');
  assert(bobKey?.startsWith('anm_mkt_'),'bob got scoped API key');

  // Tamper test: wrong signature must be rejected.
  const chT = await j('GET', `/auth/challenge?address=${alice.address}`);
  const badSig = signMsg(bob.kp, chT.data.challenge); // bob signs alice's challenge
  const bad = await j('POST','/accounts/register',{ address:alice.address, challenge:chT.data.challenge, signature:badSig, publicKey:alice.pubHex });
  assert(bad.status===401,'forged signature (bob signs, alice pubkey) is REJECTED');

  console.log('\n[2] seed balance via admin grant (ledger-only)');
  await j('POST','/admin/grant',{address:alice.address, amountAnm:'1000'},{'x-admin-token':ADMIN});
  const ab0 = await j('GET','/balance',null,auth(aliceKey));
  assert(ab0.data.balanceAnm==='1000','alice balance = 1000 ANM');

  console.log('\n[3] agent profiles (AADP discovery)');
  await j('POST','/agents',{handle:'hirer-bot', summary:'coordinates work', skills:['coordination']},auth(aliceKey));
  await j('POST','/agents',{handle:'designer', summary:'makes images', skills:['image generation','design']},auth(bobKey));
  const disc = await j('GET','/agents?skill=image');
  assert(disc.data.agents?.some(a=>a.handle==='designer'),'discovery finds "designer" by skill=image');

  console.log('\n[4] Bob publishes a paid RAG listing; Alice (AI->AI) buys it; 80/20 split');
  const create = await j('POST','/listings',{ name:'Legal RAG', type:'RAG_ASSISTANT', category:'Finance', tagline:'contract law answers', systemPrompt:'You are a legal assistant.', prices:[{model:'ONE_TIME', amountNanm:'100000000000'}] }, auth(bobKey)); // 100 ANM
  assert(create.status===201,'bob created listing');
  const slug = create.data.listing.slug;
  await j('POST',`/listings/${slug}/publish`,{},auth(bobKey));
  const buy = await j('POST','/purchases',{slug, },auth(aliceKey));
  assert(buy.status===201,'alice purchased access');
  const ab1 = await j('GET','/balance',null,auth(aliceKey));
  const bb1 = await j('GET','/balance',null,auth(bobKey));
  assert(ab1.data.balanceAnm==='900','alice balance 1000 -> 900 (paid 100)');
  assert(bb1.data.balanceAnm==='80','bob balance -> 80 (80% of 100; 20% to treasury)');

  console.log('\n[5] escrowed agent-to-agent task: Alice hires designer for 50 ANM');
  const task = await j('POST','/tasks',{title:'Make a logo', brief:'blue, minimal', amountNanm:'50000000000', workerHandle:'designer'},auth(aliceKey));
  assert(task.status===201,'task opened + escrow funded');
  const taskId = task.data.task.id;
  const ab2 = await j('GET','/balance',null,auth(aliceKey));
  assert(ab2.data.balanceAnm==='850','alice 900 -> 850 (50 escrowed)');
  const acc = await j('POST',`/tasks/${taskId}`,{action:'accept'},auth(bobKey));
  assert(acc.status===200,'bob accepted the task');
  await j('POST',`/tasks/${taskId}`,{action:'deliver', deliverable:{url:'ipfs://logo'}},auth(bobKey));
  const rel = await j('POST',`/tasks/${taskId}`,{action:'release', rating:5},auth(aliceKey));
  assert(rel.status===200,'alice released escrow with 5-star rating');
  const bb2 = await j('GET','/balance',null,auth(bobKey));
  assert(bb2.data.balanceAnm==='120','bob 80 -> 120 (received 40 net of 50, 20% fee)');

  console.log('\n[6] reputation accrued from the completed task');
  const rep = await j('GET','/agents?q=designer');
  const d = rep.data.agents?.find(a=>a.handle==='designer');
  assert(d && d.tasksCompleted===1,'designer tasksCompleted = 1');
  assert(d && d.reputation===10000,'designer reputation = 100% (1/1 reliable)');

  console.log('\n[7] ledger conservation check');
  const led = await j('GET','/ledger',null,auth(bobKey));
  const sum = led.data.entries.reduce((s,e)=>s+BigInt(e.deltaNanm),0n);
  assert(sum===120000000000n,'bob ledger deltas sum to balance (120 ANM)');

  console.log('\n[8] agent messaging');
  const msg = await j('POST','/agents/messages',{to:'designer', body:'great work!', intent:'message'},auth(aliceKey));
  assert(msg.status===201,'alice messaged designer');
  const inbox = await j('GET','/agents/messages',null,auth(bobKey));
  assert(inbox.data.messages?.length>=1,'designer inbox has the message');

  console.log('\n=== done ===');
})().catch(e=>{ console.error('FATAL', e); process.exit(1); });
