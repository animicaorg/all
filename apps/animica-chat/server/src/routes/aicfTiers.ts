// GET /api/aicf/tiers — list user-facing model tiers with availability +
// access gating. Proxies the bridge's /v1/tiers (which probes the chain
// for active workers) and overlays subscription-based access rules:
//
//   - tiny + small: always allowed for any authenticated user
//   - flagship + large: require an ACTIVE subscription
//
// Each tier comes back with:
//   { code, label, description, available, allowed, requiresPro,
//     unavailableReason? }
//
// `available=false` greys out the tier in the picker even for Pro users
// when no worker is currently registered for the underlying chain tier.

import { Router } from 'express';
import { requireAuth, loadSubscription } from '../middleware/auth';
import { env } from '../env';

export const aicfTiersRouter = Router();

interface BridgeTier {
  code: string;
  label: string;
  description: string;
  chain_tier: string;
  requires_pro: boolean;
  available: boolean;
}

async function fetchBridgeTiers(): Promise<BridgeTier[]> {
  const url = `${env.AI_BASE_URL.replace(/\/$/, '')}/tiers`;
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${env.AI_API_KEY}` },
    signal: AbortSignal.timeout(4000),
  });
  if (!res.ok) throw new Error(`bridge /v1/tiers ${res.status}`);
  const body = (await res.json()) as { data?: BridgeTier[] };
  return body.data ?? [];
}

aicfTiersRouter.get('/', requireAuth, loadSubscription, async (req, res) => {
  const sub = req.activeSubscription;
  const isActive = sub && (sub.status === 'ACTIVE' || sub.status === 'PAST_DUE');

  let tiers: BridgeTier[] = [];
  try {
    tiers = await fetchBridgeTiers();
  } catch (err) {
    // Bridge is the source of truth for availability. If it's down, we
    // still return the static catalog with `available=false` so the
    // user sees the tiers greyed out instead of an empty picker.
    tiers = STATIC_FALLBACK;
  }

  const out = tiers.map((t) => {
    const requiresPro = t.requires_pro;
    const allowed = requiresPro ? Boolean(isActive) : true;
    return {
      code: t.code,
      label: t.label,
      description: t.description,
      requiresPro,
      available: t.available,
      allowed,
      unavailableReason: !t.available
        ? 'No worker registered for this tier right now.'
        : !allowed
        ? 'Subscribe to Pro to unlock this tier.'
        : undefined,
    };
  });
  res.json({ tiers: out });
});

const STATIC_FALLBACK: BridgeTier[] = [
  { code: 'tiny',     label: 'Tiny',     description: '~0.5B params — fastest',         chain_tier: 'standard', requires_pro: false, available: false },
  { code: 'small',    label: 'Small',    description: '~1.5B params — balanced',         chain_tier: 'standard', requires_pro: false, available: false },
  { code: 'flagship', label: 'Flagship', description: '~7B params — highest quality',   chain_tier: 'premium',  requires_pro: true,  available: false },
  { code: 'large',    label: 'Large',    description: '~16B MoE — datacenter',          chain_tier: 'elite',    requires_pro: true,  available: false },
];
