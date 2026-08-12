import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// PUBLIC pricing for the Animica Animal livestream pipeline. The PayPal subscribe link is a
// plain hosted-plan URL (no client secret needed on our side) — the operator creates a
// $350/mo subscription plan in their PayPal dashboard and drops PAYPAL_PLAN_ID (or a full
// PAYPAL_SUBSCRIBE_URL) into the env. Until then `configured:false` so the UI shows a
// "setup pending" state instead of a dead button.
export async function GET() {
  const priceUsd = Number(process.env.ANIMAL_PRICE_USD || '350');
  const planId = (process.env.PAYPAL_PLAN_ID || '').trim();
  const explicit = (process.env.PAYPAL_SUBSCRIBE_URL || '').trim();
  const subscribeUrl = explicit
    || (planId ? `https://www.paypal.com/webapps/billing/plans/subscribe?plan_id=${encodeURIComponent(planId)}` : '');
  return NextResponse.json({
    priceUsd,
    interval: 'month',
    currency: 'USD',
    subscribeUrl,
    configured: !!subscribeUrl,
    contactEmail: process.env.ANIMAL_SALES_EMAIL || 'ai@3vdc.com',
  });
}
