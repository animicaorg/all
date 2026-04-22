import 'dotenv/config';

const baseUrl = process.env.AICF_API_BASE_URL ?? 'http://127.0.0.1:8099';
const internalSecret = process.env.AICF_INTERNAL_SECRET ?? 'dev-internal-secret';
const tickMs = Number(process.env.TREASURY_WORKER_TICK_MS ?? 10000);

async function pollTreasury() {
  const response = await fetch(`${baseUrl}/internal/treasury/snapshot`, {
    headers: {
      'x-internal-secret': internalSecret
    }
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`treasury snapshot failed: ${response.status} ${text}`);
  }

  const body = (await response.json()) as {
    treasury?: {
      availableAnmNanos?: string;
      allocatedSubsidyAnmNanos?: string;
      paidProviderAnmNanos?: string;
      protocolFeesAnmNanos?: string;
    };
  };

  const treasury = body.treasury ?? {};
  console.log(
    `[treasury-worker] available=${treasury.availableAnmNanos ?? '0'} subsidy=${treasury.allocatedSubsidyAnmNanos ?? '0'} paid_providers=${treasury.paidProviderAnmNanos ?? '0'} fees=${treasury.protocolFeesAnmNanos ?? '0'}`
  );
}

async function main() {
  console.log(`[treasury-worker] started baseUrl=${baseUrl} tickMs=${tickMs}`);
  for (;;) {
    try {
      await pollTreasury();
    } catch (error) {
      console.error('[treasury-worker] polling error', error);
    }
    await new Promise((resolve) => setTimeout(resolve, tickMs));
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
