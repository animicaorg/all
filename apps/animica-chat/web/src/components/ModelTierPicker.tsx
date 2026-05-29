import { useEffect, useState } from 'react';
import { api } from '../lib/api';

export interface TierOption {
  code: string;
  label: string;
  description: string;
  available: boolean;
  allowed: boolean;
  requiresPro: boolean;
  unavailableReason?: string;
}

const STATIC_FALLBACK: TierOption[] = [
  { code: 'tiny',     label: 'Tiny',     description: '~0.5B — fastest',     available: false, allowed: true,  requiresPro: false },
  { code: 'small',    label: 'Small',    description: '~1.5B — balanced',    available: false, allowed: true,  requiresPro: false },
  { code: 'flagship', label: 'Flagship', description: '~7B — best quality',  available: false, allowed: false, requiresPro: true },
  { code: 'large',    label: 'Large',    description: '~16B MoE',            available: false, allowed: false, requiresPro: true },
];

let _cache: { tiers: TierOption[]; ts: number } | null = null;

export async function loadTiers(force = false): Promise<TierOption[]> {
  if (!force && _cache && Date.now() - _cache.ts < 30_000) return _cache.tiers;
  try {
    const r = await api.get<{ tiers: TierOption[] }>('/api/aicf/tiers');
    _cache = { tiers: r.tiers, ts: Date.now() };
    return r.tiers;
  } catch {
    return STATIC_FALLBACK;
  }
}

export function ModelTierPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (code: string) => void;
}) {
  const [tiers, setTiers] = useState<TierOption[]>(STATIC_FALLBACK);

  useEffect(() => {
    loadTiers().then(setTiers);
  }, []);

  const current = tiers.find((t) => t.code === value);

  return (
    <div className="flex items-center gap-2">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-white/8 bg-ink-900/40 px-2 py-1.5 text-sm text-ink-100 focus:border-accent-500 focus:outline-none"
      >
        {tiers.map((t) => {
          const disabled = !t.available || !t.allowed;
          const suffix = !t.available
            ? ' — offline'
            : !t.allowed
            ? ' — Pro only'
            : '';
          return (
            <option key={t.code} value={t.code} disabled={disabled}>
              {t.label}
              {suffix}
            </option>
          );
        })}
      </select>
      {current?.description && (
        <span className="hidden text-xs text-ink-500 lg:inline">{current.description}</span>
      )}
    </div>
  );
}
