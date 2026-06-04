// Seed default Settings (admin-tunable runtime config). Idempotent.
import { PrismaClient } from "@prisma/client";

const prisma = new PrismaClient();

const DEFAULTS: Record<string, string> = {
  MARKETPLACE_FEE_PERCENT: "5",
  RIG_OFFLINE_GRACE_SECONDS: "600",
  QUOTE_TTL_SECONDS: "600",
};

async function main() {
  for (const [key, value] of Object.entries(DEFAULTS)) {
    await prisma.setting.upsert({ where: { key }, update: {}, create: { key, value } });
  }
  console.log(`  ✓ seeded ${Object.keys(DEFAULTS).length} settings`);
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
