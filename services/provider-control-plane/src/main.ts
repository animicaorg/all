import 'dotenv/config';

const baseUrl = process.env.AICF_API_BASE_URL ?? 'http://127.0.0.1:8099';
const adminToken = process.env.AICF_ADMIN_SESSION_TOKEN ?? '';
const tickMs = Number(process.env.PROVIDER_CONTROL_TICK_MS ?? 7000);

async function pollProviders() {
  if (!adminToken) {
    console.log('[provider-control] missing AICF_ADMIN_SESSION_TOKEN; skipping provider moderation checks');
    return;
  }

  const resp = await fetch(`${baseUrl}/admin/providers`, {
    headers: {
      'x-session-token': adminToken
    }
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`provider list failed: ${resp.status} ${text}`);
  }

  const body = (await resp.json()) as {
    providers?: Array<{ id: string; state: string; reputation: number; rewardBalanceAnmNanos: string }>;
  };

  const providers = body.providers ?? [];
  const quarantined = providers.filter((provider) => provider.state === 'quarantined').length;
  const lowReputation = providers.filter((provider) => provider.reputation < 25).length;
  console.log(
    `[provider-control] providers=${providers.length} quarantined=${quarantined} low_reputation=${lowReputation}`
  );
}

async function main() {
  console.log(`[provider-control] started baseUrl=${baseUrl} tickMs=${tickMs}`);
  for (;;) {
    try {
      await pollProviders();
    } catch (error) {
      console.error('[provider-control] polling error', error);
    }
    await new Promise((resolve) => setTimeout(resolve, tickMs));
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
