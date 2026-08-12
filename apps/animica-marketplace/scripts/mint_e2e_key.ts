// One-shot: create/reuse the store-e2e publisher account (bound to the e2e buyer
// wallet address so purchases and licenses land on the same identity) and mint an
// API key with publish+buy scopes. Raw key printed once — used by the acceptance
// test, then revocable via the keys API.
import { prisma } from '../lib/db';
import { createApiKey } from '../lib/apikey';

const ADDRESS = 'anim1zqpc3yta4ufc75cku6ka5sc2gc000xeca60u0txps39frnhncwssp0s4w8hyl';

async function main() {
  let account = await prisma.account.findUnique({ where: { address: ADDRESS } });
  if (!account) {
    account = await prisma.account.create({
      data: { address: ADDRESS, displayName: 'store-e2e' },
    });
  }
  const { raw, key } = await createApiKey(account.id, {
    name: 'store-e2e',
    scopes: ['read', 'use', 'publish', 'buy'] as any,
  });
  console.log(JSON.stringify({ accountId: account.id, keyId: key.id, scopes: key.scopes, raw }));
}

main().then(() => process.exit(0)).catch((e) => { console.error(e); process.exit(1); });
