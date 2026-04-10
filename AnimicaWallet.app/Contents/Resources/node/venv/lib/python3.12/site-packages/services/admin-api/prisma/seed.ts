#!/usr/bin/env tsx
/**
 * Seed Script - Create Initial Admin
 * Usage: tsx prisma/seed.ts
 */

import { PrismaClient } from '@prisma/client';
import { hashPassword, generateTotpSecret } from '../src/utils/crypto.js';

const prisma = new PrismaClient();

async function main() {
  console.log('Seeding database...');

  // Create SUPERADMIN
  const passwordHash = await hashPassword('Admin123!');
  const totpSecret = generateTotpSecret();

  const superadmin = await prisma.admin.upsert({
    where: { email: 'admin@animica.io' },
    update: {},
    create: {
      email: 'admin@animica.io',
      passwordHash,
      totpSecretEncrypted: totpSecret, // In production, encrypt this
      role: 'SUPERADMIN',
      status: 'ACTIVE',
    },
  });

  console.log('Created SUPERADMIN:', {
    id: superadmin.id,
    email: superadmin.email,
    role: superadmin.role,
  });

  console.log('\n⚠️  IMPORTANT: Save these credentials securely!');
  console.log('Email:', superadmin.email);
  console.log('Password: Admin123!');
  console.log('TOTP Secret (base32):', totpSecret);
  console.log('\nAdd the TOTP secret to your authenticator app (Google Authenticator, Authy, etc.)');
  console.log('\n✓ Seeding complete!\n');
}

main()
  .catch((error) => {
    console.error('Seeding failed:', error);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
