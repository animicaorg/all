// Mint an API key from the command line — the operator path until the
// authenticated dashboard lands.
//
//   tsx scripts/mint-key.ts <email> [label]
//
// Ensures a User exists for <email>, mints a key (printing the raw secret
// ONCE), and reports the credit balance (first key gets the starter grant).

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// Minimal .env loader so the standalone script sees DATABASE_URL etc. without
// pulling in a dotenv dependency. Next.js loads .env on its own; tsx doesn't.
function loadEnv() {
  try {
    const text = readFileSync(resolve(process.cwd(), ".env"), "utf8");
    for (const line of text.split("\n")) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      const eq = t.indexOf("=");
      if (eq === -1) continue;
      const k = t.slice(0, eq).trim();
      const v = t.slice(eq + 1).trim();
      if (!(k in process.env)) process.env[k] = v;
    }
  } catch {
    /* no .env — rely on the ambient environment */
  }
}

async function main() {
  loadEnv();
  const [email, label] = process.argv.slice(2);
  if (!email) {
    console.error("usage: tsx scripts/mint-key.ts <email> [label]");
    process.exit(1);
  }

  // Import AFTER env is loaded so Prisma resolves DATABASE_URL.
  const { prisma } = await import("@/server/db");
  const { mintApiKey } = await import("@/server/apiAuth");
  const { getBalance } = await import("@/server/credit");

  const user = await prisma.user.upsert({
    where: { email },
    update: {},
    create: { email },
  });

  const key = await mintApiKey(user.id, label);
  const balance = await getBalance(user.id);

  console.log("\n✅ API key minted (copy it now — it won't be shown again):\n");
  console.log(`   ${key.raw}\n`);
  console.log(`   user:    ${user.email} (${user.id})`);
  console.log(`   prefix:  ${key.prefix}`);
  console.log(`   balance: $${balance}\n`);
  await prisma.$disconnect();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
