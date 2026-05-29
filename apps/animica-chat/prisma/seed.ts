import { PrismaClient } from '@prisma/client';
import { ASSISTANT_MODES } from '../server/src/services/systemPrompts';

const prisma = new PrismaClient();

async function main() {
  // Single plan: $10/mo. PayPal plan id is filled in from env at signup
  // creation time; for the MVP we read PAYPAL_PLAN_ID_PRO from env and
  // mirror it onto the row so the API doesn't need the env at runtime.
  const paypalPlanId = process.env.PAYPAL_PLAN_ID_PRO || null;

  await prisma.subscriptionPlan.upsert({
    where: { code: 'pro' },
    create: {
      code: 'pro',
      name: 'Animica Chat Pro',
      priceUsdCents: 1000,
      weeklyMessages: 1000,
      description:
        'AI chat, coding help, content tools, Animica ecosystem assistant, and private chat history.',
      features: [
        '1,000 AI messages / week',
        'GitHub + GitLab agent tools',
        'Multiple assistant modes (general, coding, marketing, blockchain helper)',
        'Private chat history',
        'Early access to new tools',
      ],
      paypalPlanId,
    },
    update: {
      name: 'Animica Chat Pro',
      priceUsdCents: 1000,
      weeklyMessages: 1000,
      paypalPlanId: paypalPlanId ?? undefined,
    },
  });

  for (const mode of ASSISTANT_MODES) {
    await prisma.systemPrompt.upsert({
      where: { code: mode.code },
      create: {
        code: mode.code,
        name: mode.name,
        prompt: mode.defaultPrompt,
        description: mode.description,
        enabled: true,
      },
      update: {
        name: mode.name,
        prompt: mode.defaultPrompt,
        description: mode.description,
      },
    });
  }

  // eslint-disable-next-line no-console
  console.log('Seed complete.');
}

main()
  .catch((e) => {
    // eslint-disable-next-line no-console
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
