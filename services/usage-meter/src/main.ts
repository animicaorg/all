import 'dotenv/config';

const baseUrl = process.env.AICF_API_BASE_URL ?? 'http://127.0.0.1:8099';
const adminToken = process.env.AICF_ADMIN_SESSION_TOKEN ?? '';
const tickMs = Number(process.env.USAGE_METER_TICK_MS ?? 5000);

async function pollUsage() {
  if (!adminToken) {
    console.log('[usage-meter] AICF_ADMIN_SESSION_TOKEN not provided; skipping admin usage polling');
    return;
  }

  const usageResp = await fetch(`${baseUrl}/usage`, {
    headers: {
      'x-session-token': adminToken
    }
  });

  if (!usageResp.ok) {
    const body = await usageResp.text();
    throw new Error(`usage fetch failed: ${usageResp.status} ${body}`);
  }

  const usage = (await usageResp.json()) as { usage?: Array<{ chargedAnmNanos: string }> };
  const count = usage.usage?.length ?? 0;
  const charged = (usage.usage ?? []).reduce((acc, row) => acc + BigInt(row.chargedAnmNanos ?? '0'), 0n);

  console.log(`[usage-meter] records=${count} charged_anm_nanos=${charged.toString()}`);
}

async function main() {
  console.log(`[usage-meter] started baseUrl=${baseUrl} tickMs=${tickMs}`);
  for (;;) {
    try {
      await pollUsage();
    } catch (error) {
      console.error('[usage-meter] polling error', error);
    }
    await new Promise((resolve) => setTimeout(resolve, tickMs));
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
