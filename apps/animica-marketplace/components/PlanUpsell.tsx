'use client';

// Contextual upgrade CTA for HTTP 402 `plan_limit` errors (lib/plan.ts planLimitError):
// the envelope carries {feature, plan, limit, used, requiredPlan, upgradeUrl} in
// error.details, and this box turns that into "here's the plan that unlocks it".
// Rendered inline wherever a gated action fails — never as an interruption of free AI flows.

// Display-only price map (checkout always resolves the real price server-side from the
// Plan row — see /api/mkt/v1/billing/subscribe).
export const PLAN_PRICE_CENTS: Record<string, number> = {
  free: 0,
  starter: 999,
  pro: 2999,
  operator: 7999,
  business: 19999,
};

export function planName(key: string | null | undefined): string {
  const k = String(key || 'pro');
  return k.charAt(0).toUpperCase() + k.slice(1);
}

export function planUsdMonthly(key: string | null | undefined): string {
  const cents = PLAN_PRICE_CENTS[String(key || 'pro')] ?? 0;
  return `$${(cents / 100).toFixed(2)}/month`;
}

export interface PlanLimitDetails {
  feature?: string;
  plan?: string;
  limit?: number | boolean;
  used?: number;
  requiredPlan?: string;
  upgradeUrl?: string;
}

// Extract {message, details} from an error whose fetch helper attached the envelope's
// code/details (see the api() helpers on the pricing/billing/workers pages). Returns null
// for anything that is not a plan_limit error so callers fall through to normal rendering.
export function planLimitOf(e: unknown): { message: string; details: PlanLimitDetails } | null {
  const any = e as any;
  if (any?.code !== 'plan_limit') return null;
  return { message: String(any.message || 'This feature needs a bigger plan.'), details: any.details ?? {} };
}

export default function PlanUpsell({
  code,
  message,
  details,
  ctaLabel,
  style,
}: {
  code?: string; // when provided, anything other than 'plan_limit' renders nothing
  message: string;
  details?: PlanLimitDetails;
  ctaLabel?: string; // override the default "Get <Plan> — $X/month"
  style?: React.CSSProperties;
}) {
  if (code && code !== 'plan_limit') return null;
  const required = details?.requiredPlan || 'pro';
  const href = details?.upgradeUrl || '/pricing';
  const showMeter = typeof details?.used === 'number' && typeof details?.limit === 'number';
  return (
    <div
      style={{
        border: '1px solid rgba(108,92,255,0.45)',
        background: 'rgba(108,92,255,0.07)',
        borderRadius: 10,
        padding: '13px 15px',
        marginTop: 12,
        ...style,
      }}
    >
      <div style={{ fontSize: 13.5, lineHeight: 1.55, color: 'var(--text)' }}>{message}</div>
      {showMeter && (
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          Using {Number(details!.used).toLocaleString('en-US')} of {Number(details!.limit).toLocaleString('en-US')} on
          your {planName(details?.plan || 'free')} plan.
        </div>
      )}
      <a className="btn primary" href={href} style={{ marginTop: 10, fontSize: 13, padding: '8px 14px' }}>
        {ctaLabel ?? `Get ${planName(required)} — ${planUsdMonthly(required)}`}
      </a>
    </div>
  );
}
