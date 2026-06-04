// Seed: create the first ADMIN user. Run: pnpm db:seed
// Env: SEED_ADMIN_EMAIL + SEED_ADMIN_PASSWORD (falls back to a dev default).
import { PrismaClient } from "@prisma/client";
import bcrypt from "bcryptjs";

const prisma = new PrismaClient();

async function main() {
  const email = (process.env.SEED_ADMIN_EMAIL || "admin@animica.org").toLowerCase();
  const password = process.env.SEED_ADMIN_PASSWORD || "changeme-admin-123";
  const passwordHash = await bcrypt.hash(password, 12);

  const user = await prisma.user.upsert({
    where: { email },
    update: { role: "ADMIN", isActive: true },
    create: { email, passwordHash, role: "ADMIN", displayName: "Animica Admin" },
  });
  console.log(`  ✓ admin user ready: ${user.email} (role=${user.role})`);

  // Seed provider configs (disabled by default except mock).
  const providers = [
    { name: "mock", isEnabled: true, priority: 1000 },
    { name: "bittensor", isEnabled: false, priority: 10 }, // Chutes SN64 direct
    { name: "openrouter", isEnabled: false, priority: 15 }, // chutes-pinned fallback
    { name: "targon", isEnabled: false, priority: 18 }, // SN4 — off until rate card confirmed
    { name: "runpod", isEnabled: false, priority: 20 },
    { name: "akash", isEnabled: false, priority: 30 },
    { name: "lambda", isEnabled: false, priority: 40 },
    { name: "animica_worker", isEnabled: false, priority: 50 },
  ];
  for (const p of providers) {
    await prisma.providerConfig.upsert({ where: { name: p.name }, update: {}, create: p });
  }
  console.log(`  ✓ seeded ${providers.length} provider configs`);

  // No demo rental listings — the marketplace only shows REAL rentals backed
  // by a live, heartbeating worker (see RentalsService.listListings).
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
